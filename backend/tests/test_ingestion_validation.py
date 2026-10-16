"""Unit tests for the shared validation path (FR-04).

These exercise :meth:`IngestionService.build_item` with an in-memory device
cache and no database, which is exactly how the MQTT hot path runs.
"""

from __future__ import annotations

import pytest

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
