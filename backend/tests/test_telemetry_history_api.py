"""API tests for the historical query endpoints (Phase 3).

Seeding goes through the HTTP ingestion endpoint on purpose: it shares the
validation and persistence path with MQTT, so what these tests read back is
exactly what a live device would have produced.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _register(client, device_id: str, cell_count: int = 3) -> None:
    response = client.post(
        "/api/v1/devices",
        json={
            "device_id": device_id,
            "name": "History test",
            "cell_count": cell_count,
            "sampling_frequency_hz": 1.0,
            "status": "active",
        },
    )
    assert response.status_code == 201, response.text


def _payload(
    device_id: str,
    message_id: str,
    measured_at: datetime,
    cells: list[float] | None = None,
) -> dict:
    cells = cells or [4.05, 4.04, 4.06]
    return {
        "schema_version": 1,
        "message_id": message_id,
        "device_id": device_id,
        "measured_at": measured_at.isoformat(),
        "pack_voltage": round(sum(cells), 3),
        "current": 1.25,
        "temperature": 29.4,
        "cell_voltages": cells,
    }


def _seed(client, device_id: str, count: int) -> list[datetime]:
    stamps = [BASE + timedelta(minutes=i) for i in range(count)]
    for i, stamp in enumerate(stamps):
        response = client.post(
            f"/api/v1/devices/{device_id}/telemetry",
            json=_payload(device_id, f"{device_id}-{i:08d}", stamp),
        )
        assert response.status_code == 201, response.text
    return stamps


def test_history_defaults_to_newest_first_and_latest_matches(
    client, require_db
) -> None:
    device_id = f"HIST_{uuid4().hex[:8]}"
    _register(client, device_id)
    _seed(client, device_id, 5)

    page = client.get(f"/api/v1/telemetry/history/{device_id}")
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["order"] == "desc"
    assert body["count"] == 5
    assert body["next_cursor"] is None
    stamps = [item["measured_at"] for item in body["items"]]
    assert stamps == sorted(stamps, reverse=True)

    latest = client.get(f"/api/v1/telemetry/latest/{device_id}")
    assert latest.status_code == 200, latest.text
    assert latest.json()["measured_at"] == body["items"][0]["measured_at"]


def test_history_keyset_pagination_covers_every_row_once(client, require_db) -> None:
    device_id = f"HIST_{uuid4().hex[:8]}"
    _register(client, device_id)
    _seed(client, device_id, 5)

    seen: list[int] = []
    cursor: str | None = None
    for _ in range(10):  # safety bound
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = client.get(
            f"/api/v1/telemetry/history/{device_id}", params=params
        ).json()
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert cursor is None
    assert len(seen) == 5
    assert len(set(seen)) == 5


def test_history_ascending_order(client, require_db) -> None:
    device_id = f"HIST_{uuid4().hex[:8]}"
    _register(client, device_id)
    _seed(client, device_id, 4)

    body = client.get(
        f"/api/v1/telemetry/history/{device_id}", params={"order": "asc"}
    ).json()
    stamps = [item["measured_at"] for item in body["items"]]
    assert stamps == sorted(stamps)


def test_history_time_range_is_inclusive(client, require_db) -> None:
    device_id = f"HIST_{uuid4().hex[:8]}"
    _register(client, device_id)
    _seed(client, device_id, 5)

    body = client.get(
        f"/api/v1/telemetry/history/{device_id}",
        params={
            "from": (BASE + timedelta(minutes=1)).isoformat(),
            "to": (BASE + timedelta(minutes=3)).isoformat(),
            "order": "asc",
        },
    ).json()
    assert body["count"] == 3
    stamps = [datetime.fromisoformat(item["measured_at"]) for item in body["items"]]
    assert stamps[0] == BASE + timedelta(minutes=1)
    assert stamps[-1] == BASE + timedelta(minutes=3)


def test_history_rejects_unknown_device(client, require_db) -> None:
    response = client.get("/api/v1/telemetry/history/NOPE_XYZ")
    assert response.status_code == 404, response.text


def test_history_rejects_bad_cursor(client, require_db) -> None:
    device_id = f"HIST_{uuid4().hex[:8]}"
    _register(client, device_id)
    response = client.get(
        f"/api/v1/telemetry/history/{device_id}", params={"cursor": "not-a-cursor"}
    )
    assert response.status_code == 400, response.text


def test_history_rejects_from_after_to(client, require_db) -> None:
    device_id = f"HIST_{uuid4().hex[:8]}"
    _register(client, device_id)
    response = client.get(
        f"/api/v1/telemetry/history/{device_id}",
        params={
            "from": (BASE + timedelta(minutes=5)).isoformat(),
            "to": BASE.isoformat(),
        },
    )
    assert response.status_code == 400, response.text


def test_latest_is_404_when_device_has_no_samples(client, require_db) -> None:
    device_id = f"HIST_{uuid4().hex[:8]}"
    _register(client, device_id)
    response = client.get(f"/api/v1/telemetry/latest/{device_id}")
    assert response.status_code == 404, response.text


def test_summary_reports_coverage_and_rejections(client, require_db) -> None:
    device_id = f"HIST_{uuid4().hex[:8]}"
    _register(client, device_id)
    stamps = _seed(client, device_id, 3)

    rejected = client.post(
        f"/api/v1/devices/{device_id}/telemetry",
        json=_payload(device_id, f"{device_id}-bad", BASE, cells=[4.05, 4.04]),
    )
    assert rejected.status_code == 422, rejected.text

    summary = client.get(f"/api/v1/telemetry/summary/{device_id}")
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["device_id"] == device_id
    assert body["sample_count"] == 3
    assert body["rejection_count"] == 1
    assert body["rejections_by_reason"] == {"cell_count_mismatch": 1}
    assert datetime.fromisoformat(body["first_measured_at"]) == stamps[0]
    assert datetime.fromisoformat(body["last_measured_at"]) == stamps[-1]
