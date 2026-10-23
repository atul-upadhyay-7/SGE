"""Integration tests for idempotent persistence and quarantine (FR-02, FR-05)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.db.database import SessionLocal
from app.db.models import TelemetryRejection, TelemetrySample
from app.repositories.device_repository import DeviceRepository
from app.repositories.telemetry_repository import TelemetryRepository
from app.schemas.telemetry import RejectionRecord, ValidatedSample
from sqlalchemy import func, select

pytestmark = pytest.mark.usefixtures("require_db")


async def _make_device(session, device_id: str) -> None:
    await DeviceRepository(session).create(
        device_id=device_id,
        name="test device",
        cell_count=3,
        sampling_frequency_hz=1.0,
        status="active",
    )
    await session.commit()


def _sample(device_id: str, message_id: str, current: float = 1.0) -> ValidatedSample:
    return ValidatedSample(
        device_id=device_id,
        message_id=message_id,
        measured_at=datetime.now(UTC),
        current=current,
        temperature=29.0,
        cell_voltages=[4.05, 4.04, 4.06],
    )


async def test_redelivery_is_idempotent() -> None:
    device_id = f"BMS_{uuid4().hex[:8]}"
    message_id = f"{device_id}-00000001"
    async with SessionLocal() as session:
        await _make_device(session, device_id)
        repository = TelemetryRepository(session)

        first = await repository.insert_batch([_sample(device_id, message_id)])
        second = await repository.insert_batch([_sample(device_id, message_id)])
        await session.commit()

    assert first.inserted == 1
    assert second.inserted == 0
    assert second.duplicates == 1

    async with SessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(TelemetrySample)
            .where(TelemetrySample.device_id == device_id)
        )
    assert count == 1


async def test_conflicting_duplicate_is_detected() -> None:
    device_id = f"BMS_{uuid4().hex[:8]}"
    message_id = f"{device_id}-00000002"
    async with SessionLocal() as session:
        await _make_device(session, device_id)
        repository = TelemetryRepository(session)

        await repository.insert_batch([_sample(device_id, message_id, current=1.0)])
        outcome = await repository.insert_batch(
            [_sample(device_id, message_id, current=9.0)]
        )

    assert outcome.duplicates == 1
    assert len(outcome.conflicting) == 1
    assert outcome.conflicting[0].message_id == message_id


async def test_rejections_are_persisted_with_reason() -> None:
    marker = f"unknown_{uuid4().hex[:8]}"
    async with SessionLocal() as session:
        await TelemetryRepository(session).insert_rejections(
            [
                RejectionRecord(
                    reason="unknown_device",
                    detail="device is not registered",
                    device_id=marker,
                    topic=f"bms/{marker}/telemetry",
                    raw_payload={"message_id": "x"},
                )
            ]
        )
        await session.commit()

    async with SessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(TelemetryRejection)
            .where(TelemetryRejection.device_id == marker)
        )
    assert count == 1


async def test_batch_insert_persists_all_valid_samples() -> None:
    device_id = f"BMS_{uuid4().hex[:8]}"
    async with SessionLocal() as session:
        await _make_device(session, device_id)
        outcome = await TelemetryRepository(session).insert_batch(
            [_sample(device_id, f"{device_id}-a"), _sample(device_id, f"{device_id}-b")]
        )
        await session.commit()

    assert outcome.inserted == 2

    async with SessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(TelemetrySample)
            .where(TelemetrySample.device_id == device_id)
        )
    assert count == 2
