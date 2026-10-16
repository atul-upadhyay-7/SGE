"""API tests for optional HTTP ingestion (FR-02).

The HTTP endpoint shares validation and persistence with the MQTT path; these
tests assert the observable verdicts (stored / duplicate / rejected).
"""

from __future__ import annotations

from uuid import uuid4


def _register(client, device_id: str, cell_count: int = 3) -> None:
    response = client.post(
        "/api/v1/devices",
        json={
            "device_id": device_id,
            "name": "HTTP ingest test",
            "cell_count": cell_count,
            "sampling_frequency_hz": 1.0,
            "status": "active",
        },
    )
    assert response.status_code == 201, response.text


def _payload(device_id: str, message_id: str, cells: list[float] | None = None) -> dict:
    cells = cells or [4.05, 4.04, 4.06]
    return {
        "schema_version": 1,
        "message_id": message_id,
        "device_id": device_id,
        "measured_at": "2026-10-16T05:30:00Z",
        "pack_voltage": round(sum(cells), 3),
        "current": 1.25,
        "temperature": 29.4,
        "cell_voltages": cells,
    }


def test_http_ingest_stores_then_reports_duplicate(client, require_db) -> None:
    device_id = f"HTTP_{uuid4().hex[:8]}"
    _register(client, device_id)
    message_id = f"{device_id}-00000001"

    first = client.post(
        f"/api/v1/devices/{device_id}/telemetry",
        json=_payload(device_id, message_id),
    )
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "stored"

    second = client.post(
        f"/api/v1/devices/{device_id}/telemetry",
        json=_payload(device_id, message_id),
    )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "duplicate"


def test_http_ingest_rejects_wrong_cell_count(client, require_db) -> None:
    device_id = f"HTTP_{uuid4().hex[:8]}"
    _register(client, device_id)

    response = client.post(
        f"/api/v1/devices/{device_id}/telemetry",
        json=_payload(device_id, f"{device_id}-00000001", cells=[4.05, 4.04]),
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["reason"] == "cell_count_mismatch"


def test_http_ingest_rejects_unknown_device(client, require_db) -> None:
    device_id = f"HTTP_{uuid4().hex[:8]}"

    response = client.post(
        f"/api/v1/devices/{device_id}/telemetry",
        json=_payload(device_id, f"{device_id}-00000001"),
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["reason"] == "unknown_device"


def test_http_ingest_rejects_impossible_value(client, require_db) -> None:
    device_id = f"HTTP_{uuid4().hex[:8]}"
    _register(client, device_id)

    response = client.post(
        f"/api/v1/devices/{device_id}/telemetry",
        json=_payload(device_id, f"{device_id}-00000001", cells=[9.99, 4.04, 4.06]),
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["reason"] == "value_out_of_range"
