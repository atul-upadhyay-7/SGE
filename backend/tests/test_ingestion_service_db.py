"""Integration tests for the queue -> writer persistence path (FR-02)."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select

from app.core.config import Settings
from app.db.database import SessionLocal
from app.db.models import TelemetryRejection, TelemetrySample
from app.repositories.device_repository import DeviceRepository
from app.services.device_cache import DeviceCache
from app.services.ingestion_service import IngestionService


class _Device:
    def __init__(self, device_id: str, cell_count: int, status: str = "active") -> None:
        self.device_id = device_id
        self.cell_count = cell_count
        self.sampling_frequency_hz = 1.0
        self.status = status


def _payload(device_id: str, message_id: str) -> dict:
    return {
        "schema_version": 1,
        "message_id": message_id,
        "device_id": device_id,
        "measured_at": "2026-10-16T05:30:00Z",
        "pack_voltage": 12.15,
        "current": 1.25,
        "temperature": 29.4,
        "cell_voltages": [4.05, 4.04, 4.06],
    }


async def _register(device_id: str) -> None:
    async with SessionLocal() as session:
        await DeviceRepository(session).create(
            device_id=device_id,
            name="service test",
            cell_count=3,
            sampling_frequency_hz=1.0,
            status="active",
        )
        await session.commit()


async def test_ingest_enqueues_then_flush_persists(require_db) -> None:
    device_id = f"BMS_{uuid4().hex[:8]}"
    await _register(device_id)

    cache = DeviceCache()
    cache.load([_Device(device_id, 3)])
    service = IngestionService(SessionLocal, cache, Settings())

    await service.ingest(device_id, _payload(device_id, f"{device_id}-0001"))
    await service.flush_pending()

    assert service.stats()["stored"] == 1

    async with SessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(TelemetrySample)
            .where(TelemetrySample.device_id == device_id)
        )
    assert count == 1


async def test_unknown_device_is_quarantined(require_db) -> None:
    device_id = f"BMS_{uuid4().hex[:8]}"
    service = IngestionService(SessionLocal, DeviceCache(), Settings())

    await service.ingest(device_id, _payload(device_id, f"{device_id}-0001"))
    await service.flush_pending()

    assert service.stats()["rejected"] == 1

    async with SessionLocal() as session:
        reasons = list(
            await session.scalars(
                select(TelemetryRejection.reason).where(
                    TelemetryRejection.device_id == device_id
                )
            )
        )
    assert reasons == ["unknown_device"]
