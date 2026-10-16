"""API tests for device registration and lookup (FR-01)."""

from __future__ import annotations

from uuid import uuid4


def _device_body(device_id: str) -> dict:
    return {
        "device_id": device_id,
        "name": "Bench pack",
        "cell_count": 3,
        "sampling_frequency_hz": 1.0,
        "status": "active",
    }


def test_register_get_and_list(client, require_db) -> None:
    device_id = f"API_{uuid4().hex[:8]}"

    created = client.post("/api/v1/devices", json=_device_body(device_id))
    assert created.status_code == 201, created.text
    assert created.json()["device_id"] == device_id
    assert created.json()["cell_count"] == 3

    duplicate = client.post("/api/v1/devices", json=_device_body(device_id))
    assert duplicate.status_code == 409

    fetched = client.get(f"/api/v1/devices/{device_id}")
    assert fetched.status_code == 200
    assert fetched.json()["device_id"] == device_id

    listing = client.get("/api/v1/devices", params={"limit": 500})
    assert listing.status_code == 200
    assert device_id in {d["device_id"] for d in listing.json()}


def test_get_unknown_device_returns_404(client, require_db) -> None:
    response = client.get(f"/api/v1/devices/NOPE_{uuid4().hex[:8]}")
    assert response.status_code == 404
