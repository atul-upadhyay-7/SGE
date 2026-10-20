"""API tests for cycle aggregation and SoH inference (Phase 4).

Telemetry is seeded through the HTTP ingestion endpoint (the same path MQTT
uses), then aggregation is triggered over the API. The synthetic stream is a
sawtooth of discharge / charge phases chosen so each discharge fully
traverses the model's 4.0-3.4 V partial window, which is what the estimator
requires to score a cycle. Streams are kept short on purpose: every sample is
one HTTP round-trip, so the seeded devices are shared across the read-back
assertions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.cycle_service import _finite, _representative_cell

BASE = datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC)
STEP = timedelta(seconds=1)

_DISCHARGE_SAMPLES = 25
_CHARGE_SAMPLES = 6
_DISCHARGE_START_V = 4.12
_DISCHARGE_END_V = 3.0
_IDLE_V = 4.16
_DISCHARGE_A = -1.6
_CHARGE_A = 1.6


def _register(client, device_id: str, cell_count: int = 3) -> None:
    response = client.post(
        "/api/v1/devices",
        json={
            "device_id": device_id,
            "name": "Cycle test",
            "cell_count": cell_count,
            "sampling_frequency_hz": 1.0,
            "status": "active",
        },
    )
    assert response.status_code == 201, response.text


def _cells(voltage: float) -> list[float]:
    """Three cells whose weakest is the reference voltage."""
    return [
        round(voltage, 3),
        round(voltage - 0.02, 3),
        round(voltage + 0.02, 3),
    ]


def _stream(cycles: int) -> list[tuple[datetime, list[float], float]]:
    stamp = BASE
    out: list[tuple[datetime, list[float], float]] = []
    for _ in range(cycles):
        for i in range(_DISCHARGE_SAMPLES):
            frac = i / (_DISCHARGE_SAMPLES - 1)
            voltage = _DISCHARGE_START_V + (
                _DISCHARGE_END_V - _DISCHARGE_START_V
            ) * frac
            out.append((stamp, _cells(voltage), _DISCHARGE_A))
            stamp += STEP
        for _ in range(_CHARGE_SAMPLES):
            out.append((stamp, _cells(3.2), _CHARGE_A))
            stamp += STEP
    return out


def _seed(client, device_id: str, cycles: int) -> None:
    for i, (stamp, cells, current) in enumerate(_stream(cycles)):
        response = client.post(
            f"/api/v1/devices/{device_id}/telemetry",
            json={
                "schema_version": 1,
                "message_id": f"{device_id}-{i:08d}",
                "device_id": device_id,
                "measured_at": stamp.isoformat(),
                "pack_voltage": round(sum(cells), 3),
                "current": current,
                "temperature": 25.0,
                "cell_voltages": cells,
            },
        )
        assert response.status_code == 201, response.text


@pytest.fixture(scope="module")
def seeded_device(client, require_db) -> str:
    """A 4-cycle device, seeded once and reused by the read-back tests."""
    device_id = f"CYC_{uuid4().hex[:8]}"
    _register(client, device_id)
    _seed(client, device_id, cycles=4)
    return device_id


def test_aggregate_produces_cycles_and_predictions(client, seeded_device) -> None:
    device_id = seeded_device

    result = client.post(f"/api/v1/devices/{device_id}/cycles/aggregate")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["device_id"] == device_id
    assert body["cycles"] == 4
    assert body["predictions"] == 4
    assert body["refused"] == 0
    assert body["samples"] > 0

    cycles = client.get(f"/api/v1/devices/{device_id}/cycles").json()
    assert cycles["count"] == 4
    numbers = [item["cycle_number"] for item in cycles["items"]]
    assert numbers == [1, 2, 3, 4]
    for item in cycles["items"]:
        assert item["feature_schema_version"] == "window-v1"
        assert item["end_time"] is not None
        assert item["start_time"] < item["end_time"]

    soh = client.get(f"/api/v1/predictions/history/{device_id}").json()
    assert soh["count"] == 4
    for item in soh["items"]:
        assert item["feature_schema_version"] == "window-v1"
        assert isinstance(item["predicted_soh"], float)
        assert item["cycle_id"] is not None

    latest = client.get(f"/api/v1/predictions/latest/{device_id}")
    assert latest.status_code == 200, latest.text
    assert latest.json()["predicted_soh"] == soh["items"][-1]["predicted_soh"]


def test_aggregation_is_idempotent(client, seeded_device) -> None:
    device_id = seeded_device

    first = client.post(
        f"/api/v1/devices/{device_id}/cycles/aggregate"
    ).json()
    second = client.post(
        f"/api/v1/devices/{device_id}/cycles/aggregate"
    ).json()

    assert second["cycles"] == first["cycles"] == 4
    assert second["predictions"] == first["predictions"] == 4
    assert (
        client.get(f"/api/v1/devices/{device_id}/cycles").json()["count"] == 4
    )


def test_too_few_cycles_are_refused_not_guessed(client, require_db) -> None:
    device_id = f"CYC_{uuid4().hex[:8]}"
    _register(client, device_id)
    _seed(client, device_id, cycles=2)

    body = client.post(
        f"/api/v1/devices/{device_id}/cycles/aggregate"
    ).json()
    assert body["cycles"] == 2
    assert body["predictions"] == 0
    assert body["refused"] == 2

    latest = client.get(f"/api/v1/predictions/latest/{device_id}")
    assert latest.status_code == 404, latest.text


def test_aggregate_rejects_unknown_device(client, require_db) -> None:
    response = client.post("/api/v1/devices/NOPE_XYZ/cycles/aggregate")
    assert response.status_code == 404, response.text


def test_cycles_and_soh_reject_unknown_device(client, require_db) -> None:
    assert client.get("/api/v1/devices/NOPE_XYZ/cycles").status_code == 404
    assert client.get("/api/v1/predictions/history/NOPE_XYZ").status_code == 404


def test_representative_cell_is_the_weakest() -> None:
    sample = SimpleNamespace(cell_voltages=[4.10, 3.98, 4.05])
    assert _representative_cell(sample) == 3.98
    assert _representative_cell(SimpleNamespace(cell_voltages=[])) is None


def test_finite_rejects_non_numbers() -> None:
    assert _finite(1.5) == 1.5
    assert _finite(None) is None
    assert _finite(float("nan")) is None
    assert _finite(float("inf")) is None
