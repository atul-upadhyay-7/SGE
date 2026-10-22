"""Integration tests for the anomaly APIs (CR §3, §4, §6).

Detection and recurrence semantics are covered by ``test_anomaly_rules`` and
``test_ingestion_service_db``; these tests seed events directly through the
repository and exercise the read and audit endpoints, including the rule that
literal collection paths are never captured by ``/anomalies/{id}``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db.database import SessionLocal
from app.repositories.anomaly_repository import AnomalyRepository
from app.repositories.device_repository import DeviceRepository

UTC = timezone.utc


async def _seed(device_id: str, *, cell_voltage_max_v: float = 4.0) -> dict:
    """Register a device and open two events and one resolved event."""
    async with SessionLocal() as session:
        await DeviceRepository(session).create(
            device_id=device_id,
            name="anomaly api test",
            cell_count=3,
            sampling_frequency_hz=1.0,
            chemistry="li_ion",
            cell_voltage_max_v=cell_voltage_max_v,
            status="active",
        )
        repository = AnomalyRepository(session)
        base = datetime(2026, 10, 16, 6, 0, tzinfo=UTC)

        open_cell = await repository.open_or_extend(
            {
                "device_id": device_id,
                "anomaly_type": "cell_voltage_out_of_range",
                "affected_cell": 1,
                "severity": "critical",
                "detected_at": base,
                "last_detected_at": base + timedelta(seconds=30),
                "duration_seconds": 30.0,
                "observed_value": 4.25,
                "expected_value": 4.0,
                "threshold": 4.0,
                "diagnostic_evidence": {"limit": "max", "cell": 1},
                "likely_causes": [{"cause": "overcharge", "confirmed": False}],
                "recommended_actions": ["reduce charge current"],
                "rule_version": "rules-v1",
            }
        )
        open_temp = await repository.open_or_extend(
            {
                "device_id": device_id,
                "anomaly_type": "temperature_out_of_range",
                "affected_cell": None,
                "severity": "warning",
                "detected_at": base + timedelta(hours=1),
                "last_detected_at": base + timedelta(hours=1),
                "duration_seconds": 0.0,
                "observed_value": 55.0,
                "expected_value": 45.0,
                "threshold": 45.0,
                "diagnostic_evidence": {"limit": "max"},
                "likely_causes": [{"cause": "high load", "confirmed": False}],
                "recommended_actions": ["reduce load"],
                "rule_version": "rules-v1",
            }
        )
        resolved = await repository.open_or_extend(
            {
                "device_id": device_id,
                "anomaly_type": "cell_voltage_out_of_range",
                "affected_cell": 2,
                "severity": "warning",
                "detected_at": base - timedelta(hours=2),
                "last_detected_at": base - timedelta(hours=2),
                "duration_seconds": 0.0,
                "observed_value": 4.1,
                "expected_value": 4.0,
                "threshold": 4.0,
                "diagnostic_evidence": {"limit": "max", "cell": 2},
                "likely_causes": [],
                "recommended_actions": [],
                "rule_version": "rules-v1",
            }
        )
        await repository.close_event(resolved, base - timedelta(hours=1))
        await session.commit()
        return {
            "open_cell": open_cell.id,
            "open_temp": open_temp.id,
            "resolved": resolved.id,
        }


@pytest.fixture(scope="module")
def seeded(client: TestClient, require_db) -> tuple[str, dict]:
    device_id = f"ANOM_{uuid4().hex[:8]}"
    import asyncio

    ids = asyncio.run(_seed(device_id))
    return device_id, ids


def test_list_anomalies_returns_the_device_events(client, seeded) -> None:
    device_id, _ = seeded
    body = client.get("/api/v1/anomalies", params={"device_id": device_id}).json()
    assert body["total"] == 3
    assert body["count"] == 3
    assert all(item["device_id"] == device_id for item in body["items"])


def test_list_filters_by_status_and_severity(client, seeded) -> None:
    device_id, _ = seeded
    open_only = client.get(
        "/api/v1/anomalies", params={"device_id": device_id, "status": "open"}
    ).json()
    assert open_only["total"] == 2
    critical = client.get(
        "/api/v1/anomalies", params={"device_id": device_id, "severity": "critical"}
    ).json()
    assert critical["total"] == 1


def test_literal_paths_are_not_captured_by_the_id_route(client, seeded) -> None:
    device_id, _ = seeded
    for path in ("summary", "hourly", "recurrence"):
        response = client.get(f"/api/v1/anomalies/{path}")
        assert response.status_code == 200, path
    summary = client.get(
        "/api/v1/anomalies/summary", params={"device_id": device_id}
    ).json()
    assert summary["total"] == 3
    assert summary["open"] == 2
    assert summary["resolved"] == 1
    assert summary["unresolved"] == 2


def test_summary_breaks_down_by_severity_and_type(client, seeded) -> None:
    device_id, _ = seeded
    summary = client.get(
        "/api/v1/anomalies/summary", params={"device_id": device_id}
    ).json()
    severities = {row["severity"]: row["count"] for row in summary["by_severity"]}
    assert severities == {"warning": 2, "critical": 1}
    types = {row["anomaly_type"]: row["count"] for row in summary["by_type"]}
    assert types["cell_voltage_out_of_range"] == 2


def test_hourly_buckets_include_severity_breakdown(client, seeded) -> None:
    device_id, _ = seeded
    body = client.get(
        "/api/v1/anomalies/hourly", params={"device_id": device_id}
    ).json()
    assert body["buckets"]
    first = body["buckets"][0]
    assert {"hour", "total", "open", "resolved", "by_severity"} <= set(first)
    assert sum(b["total"] for b in body["buckets"]) == 3


def test_hourly_counts_open_events_in_every_active_hour(client) -> None:
    """A sustained open event reads as persistent, hour by hour (CR §6)."""
    require = client.get("/api/v1/health/database")
    if require.status_code != 200:  # pragma: no cover - infra guard
        pytest.skip("database not reachable")

    device_id = f"ANOM_H_{uuid4().hex[:8]}"

    async def _seed_span() -> tuple[int, int]:
        async with SessionLocal() as session:
            await DeviceRepository(session).create(
                device_id=device_id,
                name="hourly span test",
                cell_count=3,
                sampling_frequency_hz=1.0,
                chemistry="li_ion",
                status="active",
            )
            repository = AnomalyRepository(session)
            base = datetime(2026, 10, 15, 3, 0, tzinfo=UTC)
            # Open event detected at 03:xx, last seen at 06:xx → active in
            # hours 03, 04, 05, 06.
            open_event = await repository.open_or_extend(
                {
                    "device_id": device_id,
                    "anomaly_type": "temperature_out_of_range",
                    "affected_cell": None,
                    "severity": "warning",
                    "detected_at": base + timedelta(minutes=10),
                    "last_detected_at": base + timedelta(hours=3, minutes=5),
                    "duration_seconds": 11100.0,
                    "observed_value": 60.0,
                    "expected_value": 45.0,
                    "threshold": 45.0,
                    "diagnostic_evidence": {"limit": "max"},
                    "likely_causes": [],
                    "recommended_actions": [],
                    "rule_version": "rules-v1",
                }
            )
            # Resolved event in a single hour (01:xx) that must not bleed into
            # later hours, because its activity after detection was the close.
            resolved = await repository.open_or_extend(
                {
                    "device_id": device_id,
                    "anomaly_type": "cell_voltage_out_of_range",
                    "affected_cell": 1,
                    "severity": "critical",
                    "detected_at": base - timedelta(hours=2),
                    "last_detected_at": base - timedelta(hours=2),
                    "duration_seconds": 0.0,
                    "observed_value": 4.3,
                    "expected_value": 4.0,
                    "threshold": 4.0,
                    "diagnostic_evidence": {"limit": "max", "cell": 1},
                    "likely_causes": [],
                    "recommended_actions": [],
                    "rule_version": "rules-v1",
                }
            )
            await repository.close_event(resolved, base - timedelta(hours=1))
            await session.commit()
            return open_event.id, resolved.id

    import asyncio

    asyncio.run(_seed_span())

    body = client.get(
        "/api/v1/anomalies/hourly", params={"device_id": device_id}
    ).json()
    buckets = {b["hour"][:13]: b for b in body["buckets"]}
    # 03, 04, 05, 06 carry the still-open event; 01 carries the resolved one.
    assert {"2026-10-15T01", "2026-10-15T03", "2026-10-15T04",
            "2026-10-15T05", "2026-10-15T06"} <= set(buckets)
    assert "2026-10-15T02" not in buckets  # gap: no event active that hour
    for hour in ("03", "04", "05", "06"):
        bucket = buckets[f"2026-10-15T{hour}"]
        assert bucket["total"] == 1
        assert bucket["open"] == 1
        assert bucket["by_severity"]["warning"] == 1
    # The resolved event appears only in its detection hour.
    assert buckets["2026-10-15T01"]["resolved"] == 1
    assert buckets["2026-10-15T01"]["total"] == 1


def test_recurrence_counts_repeats_per_key(client, seeded) -> None:
    device_id, _ = seeded
    body = client.get(
        "/api/v1/anomalies/recurrence", params={"device_id": device_id}
    ).json()
    entries = [
        e for e in body["entries"] if e["anomaly_type"] == "cell_voltage_out_of_range"
    ]
    # Each (device, type, cell) is its own bucket: cell 1 (open) and cell 2
    # (resolved) are one occurrence each, not merged across cells.
    by_cell = {e["affected_cell"]: e for e in entries}
    assert set(by_cell) == {1, 2}
    assert by_cell[1]["occurrences"] == 1
    assert by_cell[1]["unresolved"] == 1
    assert by_cell[2]["occurrences"] == 1
    assert by_cell[2]["unresolved"] == 0
    # Every entry is scoped to the device the filter named.
    assert all(e["device_id"] == device_id for e in body["entries"])


def test_recurrence_counts_repeated_episodes(client) -> None:
    """Two closed episodes plus one open on the same key = 3 occurrences."""
    if client.get("/api/v1/health/database").status_code != 200:  # pragma: no cover
        pytest.skip("database not reachable")

    device_id = f"ANOM_R_{uuid4().hex[:8]}"

    async def _seed_repeats() -> None:
        async with SessionLocal() as session:
            await DeviceRepository(session).create(
                device_id=device_id,
                name="recurrence test",
                cell_count=3,
                sampling_frequency_hz=1.0,
                chemistry="li_ion",
                status="active",
            )
            repository = AnomalyRepository(session)
            base = datetime(2026, 10, 14, 0, 0, tzinfo=UTC)

            def payload(detected: datetime) -> dict:
                return {
                    "device_id": device_id,
                    "anomaly_type": "temperature_out_of_range",
                    "affected_cell": None,
                    "severity": "warning",
                    "detected_at": detected,
                    "last_detected_at": detected,
                    "duration_seconds": 0.0,
                    "observed_value": 58.0,
                    "expected_value": 45.0,
                    "threshold": 45.0,
                    "diagnostic_evidence": {"limit": "max"},
                    "likely_causes": [],
                    "recommended_actions": [],
                    "rule_version": "rules-v1",
                }

            # Episode 1: opens and closes.
            first = await repository.open_or_extend(payload(base))
            await repository.close_event(first, base + timedelta(minutes=20))
            # Episode 2: opens and closes.
            second = await repository.open_or_extend(payload(base + timedelta(hours=5)))
            await repository.close_event(second, base + timedelta(hours=5, minutes=10))
            # Episode 3: still open.
            await repository.open_or_extend(payload(base + timedelta(hours=10)))
            await session.commit()

    import asyncio

    asyncio.run(_seed_repeats())

    body = client.get(
        "/api/v1/anomalies/recurrence", params={"device_id": device_id}
    ).json()
    assert len(body["entries"]) == 1
    entry = body["entries"][0]
    assert entry["anomaly_type"] == "temperature_out_of_range"
    assert entry["affected_cell"] is None
    assert entry["occurrences"] == 3
    assert entry["unresolved"] == 1
    assert entry["first_detected_at"] < entry["last_detected_at"]

    # A different device's recurrence report never leaks this one in.
    other = client.get(
        "/api/v1/anomalies/recurrence",
        params={"device_id": "ANOM_R_nonexistent"},
    ).json()
    assert other["entries"] == []


def test_get_anomaly_and_history(client, seeded) -> None:
    _, ids = seeded
    detail = client.get(f"/api/v1/anomalies/{ids['open_cell']}")
    assert detail.status_code == 200
    assert detail.json()["affected_cell"] == 1

    # The resolved event carries the detector's resolve action in its trail.
    history = client.get(f"/api/v1/anomalies/{ids['resolved']}/history").json()
    assert any(action["action_type"] == "resolve" for action in history)


def test_get_unknown_anomaly_is_404(client, seeded) -> None:
    assert client.get("/api/v1/anomalies/99999999").status_code == 404


def test_acknowledge_moves_status_and_logs_action(client, seeded) -> None:
    _, ids = seeded
    response = client.post(
        f"/api/v1/anomalies/{ids['open_temp']}/acknowledge",
        json={"actor": "operator", "note": "investigating"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "acknowledged"

    history = client.get(f"/api/v1/anomalies/{ids['open_temp']}/history").json()
    assert any(action["action_type"] == "acknowledge" for action in history)


def test_corrective_action_moves_to_investigating(client, seeded) -> None:
    _, ids = seeded
    response = client.post(
        f"/api/v1/anomalies/{ids['open_cell']}/actions",
        json={
            "action_type": "corrective_action",
            "actor": "tech",
            "note": "replaced cell",
        },
    )
    assert response.status_code == 200
    assert response.json()["action_type"] == "corrective_action"

    detail = client.get(f"/api/v1/anomalies/{ids['open_cell']}").json()
    assert detail["status"] == "investigating"


def test_actions_reject_resolve_type(client, seeded) -> None:
    _, ids = seeded
    response = client.post(
        f"/api/v1/anomalies/{ids['open_temp']}/actions",
        json={"action_type": "resolve"},
    )
    assert response.status_code == 400


def test_resolve_ends_the_episode(client, seeded) -> None:
    device_id, _ = seeded
    # A fresh event so the resolve test does not depend on ordering above.
    import asyncio

    async def _open_one() -> int:
        async with SessionLocal() as session:
            event = await AnomalyRepository(session).open_or_extend(
                {
                    "device_id": device_id,
                    "anomaly_type": "pack_voltage_out_of_range",
                    "affected_cell": None,
                    "severity": "warning",
                    "detected_at": datetime.now(UTC),
                    "last_detected_at": datetime.now(UTC),
                    "duration_seconds": 0.0,
                    "observed_value": 14.5,
                    "expected_value": 12.6,
                    "threshold": 12.6,
                    "diagnostic_evidence": {"limit": "max"},
                    "likely_causes": [],
                    "recommended_actions": [],
                    "rule_version": "rules-v1",
                }
            )
            await session.commit()
            return event.id

    anomaly_id = asyncio.run(_open_one())
    response = client.post(
        f"/api/v1/anomalies/{anomaly_id}/resolve",
        json={"actor": "operator", "note": "condition cleared"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"
    assert body["ended_at"] is not None

    again = client.post(f"/api/v1/anomalies/{anomaly_id}/resolve", json={})
    assert again.status_code == 409


def test_acknowledge_resolved_is_conflict(client, seeded) -> None:
    _, ids = seeded
    response = client.post(
        f"/api/v1/anomalies/{ids['resolved']}/acknowledge", json={}
    )
    assert response.status_code == 409


def test_range_validation_rejects_inverted_window(client, seeded) -> None:
    response = client.get(
        "/api/v1/anomalies",
        params={"from": "2026-10-17T00:00:00Z", "to": "2026-10-16T00:00:00Z"},
    )
    assert response.status_code == 400


def test_false_positive_requires_explanation_and_is_terminal(client, require_db):
    import asyncio
    device = f"FP_{uuid4().hex[:8]}"
    ids = asyncio.run(_seed(device))
    event_id = ids['open_cell']
    url = f"/api/v1/anomalies/{event_id}/resolve"
    assert client.post(url, json={'classification': 'false_positive'}).status_code == 422
    response = client.post(url, json={'classification': 'false_positive', 'outcome': 'Bench reference verified sensor offset', 'actor': 'acceptance'})
    assert response.status_code == 200
    assert response.json()['resolution_outcome'] == 'Bench reference verified sensor offset'
    assert response.json()['status'] == 'false_positive'
    assert client.post(url, json={}).status_code == 409
    assert client.post(f'/api/v1/anomalies/{event_id}/acknowledge', json={}).status_code == 409
    history = client.get(f'/api/v1/anomalies/{event_id}/history').json()
    assert history[-1]['evidence']['classification'] == 'false_positive'
    summary = client.get('/api/v1/anomalies/summary', params={'device_id': device}).json()
    assert summary['false_positive'] == 1
    assert summary['unresolved'] == 1
    filtered = client.get('/api/v1/anomalies', params={'device_id': device, 'status': 'false_positive'}).json()
    assert filtered['total'] == 1


def test_resolution_outcome_persists(client, require_db):
    import asyncio
    ids = asyncio.run(_seed(f"OUT_{uuid4().hex[:8]}"))
    url = f"/api/v1/anomalies/{ids['open_temp']}/resolve"
    response = client.post(url, json={'outcome': 'Cooling restored after fan replacement'})
    assert response.status_code == 200
    assert response.json()['resolution_outcome'] == 'Cooling restored after fan replacement'


def test_acknowledged_episode_stays_active_for_detection(client, require_db):
    import asyncio
    device = f"ACTIVE_{uuid4().hex[:8]}"
    ids = asyncio.run(_seed(device))
    event_id = ids['open_cell']
    assert client.post(f'/api/v1/anomalies/{event_id}/acknowledge', json={}).status_code == 200
    async def check():
        async with SessionLocal() as session:
            repo = AnomalyRepository(session)
            event = await repo.open_event(device, 'cell_voltage_out_of_range', 1)
            assert event.id == event_id
            assert event.status == 'acknowledged'
            assert event_id in [e.id for e in await repo.open_events(device)]
    asyncio.run(check())


def test_cell_and_recurrence_filters_match_summary_and_hourly(client, require_db):
    import asyncio
    device = f"FILTER_{uuid4().hex[:8]}"
    ids = asyncio.run(_seed(device))
    base = {'device_id': device, 'affected_cell': 1}
    page = client.get('/api/v1/anomalies', params=base).json()
    assert page['total'] == 1
    assert page['items'][0]['id'] == ids['open_cell']
    assert page['items'][0]['recurrence_count'] == 1
    assert client.get('/api/v1/anomalies/summary', params=base).json()['total'] == 1
    assert sum(b['total'] for b in client.get('/api/v1/anomalies/hourly', params=base).json()['buckets']) == 1
    repeated = {**base, 'min_recurrence': 2}
    assert client.get('/api/v1/anomalies', params=repeated).json()['total'] == 0
    assert client.get('/api/v1/anomalies/summary', params=repeated).json()['total'] == 0
    assert client.get('/api/v1/anomalies/hourly', params=repeated).json()['buckets'] == []
    pack = client.get('/api/v1/anomalies', params={'device_id': device, 'affected_cell': 0}).json()
    assert pack['total'] == 1
    assert pack['items'][0]['affected_cell'] is None
    assert client.get('/api/v1/anomalies', params={'affected_cell': -1}).status_code == 422


def test_comparison_uses_adjacent_windows_and_zero_baseline(client, require_db):
    import asyncio
    device = f"CMP_{uuid4().hex[:8]}"
    asyncio.run(_seed(device))
    q = {'device_id': device, 'from': '2026-10-16T06:00:00Z', 'to': '2026-10-16T08:00:00Z'}
    body = client.get('/api/v1/anomalies/comparison', params=q).json()
    assert body['current']['total'] == 2
    assert body['previous']['total'] == 1
    assert body['total_change_percent'] == 100
    body = client.get('/api/v1/anomalies/comparison', params={**q, 'affected_cell': 1}).json()
    assert body['current']['total'] == 1
    assert body['previous']['total'] == 0
    assert body['total_change_percent'] is None
    assert client.get('/api/v1/anomalies/comparison', params={**q, 'to': q['from']}).status_code == 400


def test_recurrence_counts_distinct_episodes_before_pagination(client, require_db):
    import asyncio
    device = f"REPEATED_{uuid4().hex[:8]}"
    ids = asyncio.run(_seed(device))
    async def repeat():
        async with SessionLocal() as session:
            repo = AnomalyRepository(session)
            first = await repo.get(ids['open_cell'])
            await repo.close_event(first, first.last_detected_at)
            fields = {k: getattr(first, k) for k in ['device_id', 'anomaly_type', 'affected_cell', 'severity', 'observed_value', 'expected_value', 'threshold', 'diagnostic_evidence', 'likely_causes', 'recommended_actions', 'rule_version']}
            fields.update(detected_at=first.detected_at + timedelta(minutes=5), last_detected_at=first.detected_at + timedelta(minutes=6), duration_seconds=60)
            await repo.open_or_extend(fields)
            await session.commit()
    asyncio.run(repeat())
    q = {'device_id': device, 'min_recurrence': 2, 'limit': 1}
    body = client.get('/api/v1/anomalies', params=q).json()
    assert body['total'] == 2
    assert len(body['items']) == 1
    assert body['items'][0]['recurrence_count'] == 2
    assert client.get('/api/v1/anomalies/summary', params=q).json()['total'] == 2
    assert client.get('/api/v1/anomalies', params={**q, 'status': 'open'}).json()['total'] == 0
