"""Unit tests for the shared validation path (FR-04).

These exercise :meth:`IngestionService.build_item` with an in-memory device
cache and no database, which is exactly how the MQTT hot path runs.
"""

from __future__ import annotations

from app.core.config import Settings
from app.schemas.telemetry import RejectionRecord, ValidatedSample
from app.services import ingestion_service as ing
from app.services.device_cache import DeviceCache
from app.services.ingestion_service import IngestionService


class _FakeSessionFactory:
    def __call__(self):  # pragma: no cover - build_item never opens a session
        raise AssertionError("validation must not touch the database")


def make_service(*devices) -> IngestionService:
    cache = DeviceCache()
    cache.load(devices)
    return IngestionService(_FakeSessionFactory(), cache, Settings())


class _Device:
    def __init__(self, device_id: str, cell_count: int, status: str = "active") -> None:
        self.device_id = device_id
        self.cell_count = cell_count
        self.sampling_frequency_hz = 1.0
        self.status = status


def payload(**overrides) -> dict:
    data = {
        "schema_version": 1,
        "message_id": "BMS_001-00000001",
        "device_id": "BMS_001",
        "measured_at": "2026-10-16T05:30:00Z",
        "pack_voltage": 12.15,
        "current": 1.25,
        "temperature": 29.4,
        "cell_voltages": [4.05, 4.04, 4.06],
    }
    data.update(overrides)
    return data


def test_valid_payload_becomes_a_sample_with_quality_flags() -> None:
    service = make_service(_Device("BMS_001", 3))
    item = service.build_item("BMS_001", payload())

    assert isinstance(item, ValidatedSample)
    assert item.message_id == "BMS_001-00000001"
    assert item.quality_flags is not None
    assert "cell_imbalance_v" in item.quality_flags


def test_unknown_device_is_rejected() -> None:
    service = make_service()
    item = service.build_item("BMS_001", payload())
    assert isinstance(item, RejectionRecord)
    assert item.reason == ing.REASON_UNKNOWN_DEVICE


def test_inactive_device_is_rejected() -> None:
    service = make_service(_Device("BMS_001", 3, status="retired"))
    item = service.build_item("BMS_001", payload())
    assert isinstance(item, RejectionRecord)
    assert item.reason == ing.REASON_DEVICE_INACTIVE


def test_device_id_mismatch_is_rejected() -> None:
    service = make_service(_Device("BMS_001", 3))
    item = service.build_item("BMS_001", payload(device_id="OTHER"))
    assert isinstance(item, RejectionRecord)
    assert item.reason == ing.REASON_DEVICE_ID_MISMATCH


def test_unsupported_schema_version_is_rejected() -> None:
    service = make_service(_Device("BMS_001", 3))
    item = service.build_item("BMS_001", payload(schema_version=2))
    assert isinstance(item, RejectionRecord)
    assert item.reason == ing.REASON_UNSUPPORTED_SCHEMA


def test_cell_count_mismatch_is_rejected() -> None:
    service = make_service(_Device("BMS_001", 4))
    item = service.build_item("BMS_001", payload())
    assert isinstance(item, RejectionRecord)
    assert item.reason == ing.REASON_CELL_COUNT_MISMATCH


def test_impossible_cell_voltage_is_rejected() -> None:
    service = make_service(_Device("BMS_001", 3))
    item = service.build_item(
        "BMS_001", payload(cell_voltages=[9.99, 4.04, 4.06])
    )
    assert isinstance(item, RejectionRecord)
    assert item.reason == ing.REASON_VALUE_OUT_OF_RANGE


def test_impossible_temperature_is_rejected() -> None:
    service = make_service(_Device("BMS_001", 3))
    item = service.build_item("BMS_001", payload(temperature=999.0))
    assert isinstance(item, RejectionRecord)
    assert item.reason == ing.REASON_VALUE_OUT_OF_RANGE


def test_missing_field_is_classified() -> None:
    service = make_service(_Device("BMS_001", 3))
    data = payload()
    del data["temperature"]
    item = service.build_item("BMS_001", data)
    assert isinstance(item, RejectionRecord)
    assert item.reason == ing.REASON_MISSING_FIELD


def test_naive_timestamp_is_classified_as_invalid_timestamp() -> None:
    service = make_service(_Device("BMS_001", 3))
    item = service.build_item(
        "BMS_001", payload(measured_at="2026-10-16T05:30:00")
    )
    assert isinstance(item, RejectionRecord)
    assert item.reason == ing.REASON_INVALID_TIMESTAMP


def test_non_finite_value_is_classified() -> None:
    service = make_service(_Device("BMS_001", 3))
    item = service.build_item("BMS_001", payload(current=float("inf")))
    assert isinstance(item, RejectionRecord)
    assert item.reason == ing.REASON_NON_FINITE


def test_legacy_frame_validates_for_single_cell_device() -> None:
    service = make_service(_Device("CELL1", 1))
    item = service.build_item(
        "CELL1",
        {"timestamp": 1_700_000_000, "voltage": 4.05, "current": 1.0, "temperature": 28.0},
    )
    assert isinstance(item, ValidatedSample)
    assert item.cell_voltages == [4.05]


def test_unusual_but_valid_sample_is_flagged_not_rejected() -> None:
    service = make_service(_Device("BMS_001", 3))
    item = service.build_item(
        "BMS_001", payload(cell_voltages=[3.40, 4.10, 4.10])
    )
    assert isinstance(item, ValidatedSample)
    assert item.quality_flags["cell_imbalance_high"] is True


def test_far_future_measured_at_is_rejected() -> None:
    """One far-future sample must not pin last_seen_at forever: the
    staleness rule reads measured_at, and last_seen_at is monotonic."""
    from datetime import UTC, datetime, timedelta

    service = make_service(_Device("BMS_001", 3))
    future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    item = service.build_item("BMS_001", payload(measured_at=future))
    assert isinstance(item, RejectionRecord)
    assert item.reason == ing.REASON_VALUE_OUT_OF_RANGE


def test_recent_measured_at_with_clock_skew_is_accepted() -> None:
    from datetime import UTC, datetime, timedelta

    service = make_service(_Device("BMS_001", 3))
    soon = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    item = service.build_item("BMS_001", payload(measured_at=soon))
    assert isinstance(item, ValidatedSample)


class _DummySession:
    async def rollback(self) -> None:
        pass

    async def commit(self) -> None:
        pass


class _DummySessionFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        return _DummySession()

    async def __aexit__(self, *exc):
        return False


async def test_ingest_now_reports_error_when_the_write_fails(monkeypatch) -> None:
    """A rolled-back flush must not be reported as "stored": the HTTP
    caller would never retry and the sample would be silently lost."""
    from app.repositories.telemetry_repository import TelemetryRepository

    async def failing_insert(self, samples):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(TelemetryRepository, "insert_batch", failing_insert)
    cache = DeviceCache()
    cache.load([_Device("BMS_001", 3)])
    service = IngestionService(_DummySessionFactory(), cache, Settings())
    outcome = await service.ingest_now("BMS_001", payload(), source="http")
    assert outcome.status == "error"
    assert outcome.reason == "storage_failed"
    assert service.write_errors == 1
