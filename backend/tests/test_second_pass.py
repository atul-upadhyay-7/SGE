"""Second-pass regression tests (audit follow-up).

Pins the fixes that do not fit the older suites' themes:

* report-queue admission reserves its slot synchronously;
* quarantined rejection payloads are size-bounded;
* a device-cache miss recovers from the database instead of
  bricking ingestion for existing devices;
* anomaly status transitions are conditional, so a terminal
  state cannot be resurrected by a stale read;
* oversized telemetry bodies are refused with 413 before parsing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.core.config import Settings
from app.db.database import SessionLocal
from app.db.models import AnomalyEvent
from app.repositories.anomaly_repository import AnomalyRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.telemetry_repository import (
    _MAX_RAW_PAYLOAD_CHARS,
    _bounded_payload,
)
from app.services.device_cache import DeviceCache
from app.services.ingestion_service import IngestionService
from app.services.report_queue import ReportQueue


def test_report_queue_reservation_is_synchronous_and_bounded() -> None:
    queue = ReportQueue(lambda: None, max_pending=2)  # type: ignore[arg-type]

    assert queue.try_reserve()
    assert queue.try_reserve()
    # No awaits happened, yet the bound already holds: this is
    # the property the old check-then-await flow lacked.
    assert not queue.try_reserve()

    queue.submit_reserved(101)
    assert not queue.try_reserve()  # 1 queued + 1 reserved = full

    queue.release_reservation()
    assert queue.try_reserve()


def test_bounded_payload_truncates_oversized_rejections() -> None:
    small = {"reason": "ok"}
    assert _bounded_payload(small) == small
    assert _bounded_payload(None) is None

    big = {"blob": "x" * (_MAX_RAW_PAYLOAD_CHARS + 500)}
    bounded = _bounded_payload(big)
    assert bounded is not None
    assert bounded["_truncated"] is True
    assert len(bounded["preview"]) == _MAX_RAW_PAYLOAD_CHARS
    assert bounded["original_chars"] > _MAX_RAW_PAYLOAD_CHARS


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


async def _register(session, device_id: str) -> None:
    await DeviceRepository(session).create(
        device_id=device_id,
        name="second-pass test",
        cell_count=3,
        sampling_frequency_hz=1.0,
        status="active",
    )
    await session.commit()


async def test_cache_miss_recovers_registered_device(require_db) -> None:
    device_id = f"BMS_{uuid4().hex[:8]}"
    async with SessionLocal() as session:
        await _register(session, device_id)

    # Deliberately empty cache: the startup load "failed".
    service = IngestionService(SessionLocal, DeviceCache(), Settings())
    outcome = await service.ingest_now(
        device_id, _payload(device_id, f"{device_id}-0001")
    )
    assert outcome.status == "stored"
    assert device_id in service._cache

    unknown = f"BMS_{uuid4().hex[:8]}"
    outcome = await service.ingest_now(
        unknown, _payload(unknown, f"{unknown}-0001")
    )
    assert outcome.status == "rejected"
    assert outcome.reason == "unknown_device"


async def test_conditional_transitions_cannot_resurrect(require_db) -> None:
    device_id = f"BMS_{uuid4().hex[:8]}"
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        await _register(session, device_id)
        event = AnomalyEvent(
            device_id=device_id,
            anomaly_type="cell_imbalance",
            severity="high",
            affected_cell=None,
            detected_at=now,
            last_detected_at=now,
            status="resolved",
            ended_at=now,
        )
        session.add(event)
        await session.commit()
        event_id = event.id

    async with SessionLocal() as session:
        repository = AnomalyRepository(session)
        changed = await repository.set_status(
            event_id, "acknowledged", allowed_from=("open",)
        )
        assert changed is False
        resolved_again = await repository.resolve_event(
            event_id,
            classification="resolved",
            outcome="duplicate resolve",
            ended_at=now,
            duration_seconds=1.0,
        )
        assert resolved_again is False
        await session.rollback()
        event = await repository.get(event_id)
        assert event is not None
        assert event.status == "resolved"


def test_oversized_telemetry_body_is_413(client) -> None:
    device_id = f"BMS_{uuid4().hex[:8]}"
    response = client.post(
        f"/api/v1/devices/{device_id}/telemetry",
        content=b'{"pad": "' + b"x" * (1_048_576 + 16) + b'"}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
