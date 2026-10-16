"""Unit tests for the telemetry payload contract and legacy adapter (FR-03)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.telemetry import (
    TelemetryPayload,
    adapt_legacy_payload,
    looks_like_legacy,
)


def valid_payload(**overrides) -> dict:
    payload = {
        "schema_version": 1,
        "message_id": "BMS001-00000001",
        "device_id": "BMS_001",
        "measured_at": "2026-10-16T05:30:00Z",
        "pack_voltage": 12.15,
        "current": 1.25,
        "temperature": 29.4,
        "cell_voltages": [4.05, 4.04, 4.06],
    }
    payload.update(overrides)
    return payload


def test_valid_payload_parses_and_normalises_to_utc() -> None:
    model = TelemetryPayload.model_validate(
        valid_payload(measured_at="2026-10-16T07:30:00+02:00")
    )
    assert model.device_id == "BMS_001"
    assert model.measured_at == datetime(2026, 10, 16, 5, 30, tzinfo=UTC)


def test_extra_optional_fields_are_preserved() -> None:
    model = TelemetryPayload.model_validate(valid_payload(soc=88.5, cycle_count=12))
    assert model.extras == {"soc": 88.5, "cycle_count": 12}


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TelemetryPayload.model_validate(
            valid_payload(measured_at="2026-10-16T05:30:00")
        )


def test_missing_required_field_is_rejected() -> None:
    data = valid_payload()
    del data["current"]
    with pytest.raises(ValidationError) as exc:
        TelemetryPayload.model_validate(data)
    assert any(e["type"] == "missing" for e in exc.value.errors())


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_scalars_are_rejected(bad: float) -> None:
    with pytest.raises(ValidationError):
        TelemetryPayload.model_validate(valid_payload(temperature=bad))


def test_non_finite_cell_voltage_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TelemetryPayload.model_validate(valid_payload(cell_voltages=[4.05, float("nan")]))


def test_empty_cell_voltages_are_rejected() -> None:
    with pytest.raises(ValidationError):
        TelemetryPayload.model_validate(valid_payload(cell_voltages=[]))


def test_legacy_adapter_maps_single_cell_frame() -> None:
    legacy = {
        "timestamp": 1_700_000_000,
        "voltage": 4.05,
        "current": 1.1,
        "temperature": 28.0,
    }
    assert looks_like_legacy(legacy) is True

    model = TelemetryPayload.model_validate(adapt_legacy_payload(legacy, "CELL1"))

    assert model.device_id == "CELL1"
    assert model.cell_voltages == [4.05]
    assert model.message_id == "CELL1-1700000000000"
    assert model.measured_at == datetime.fromtimestamp(1_700_000_000, tz=UTC)


def test_canonical_payload_is_not_treated_as_legacy() -> None:
    assert looks_like_legacy(valid_payload()) is False
