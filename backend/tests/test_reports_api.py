"""Integration tests for the historical report APIs (CR §8, PRD §4.7).

A report is generated once and stored, so these tests cover three things that
matter beyond "it returns 200": the snapshot actually contains the sections the
request enabled, the CSV download projects the primary table with a header row,
and the failure modes (unknown device, inverted window, bad format, unknown id)
are the correct 4xx rather than an empty report.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db.database import SessionLocal
from app.repositories.anomaly_repository import AnomalyRepository
from app.repositories.cycle_repository import CycleRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.telemetry_repository import TelemetryRepository
from app.schemas.telemetry import ValidatedSample

UTC = timezone.utc
BASE = datetime(2026, 10, 17, 6, 0, tzinfo=UTC)


def _sample(device_id: str, index: int) -> ValidatedSample:
    measured_at = BASE + timedelta(seconds=index)
    return ValidatedSample(
        device_id=device_id,
        message_id=f"msg-{index}",
        measured_at=measured_at,
        pack_voltage=48.0 + index * 0.1,
        current=5.0 - index * 0.05,
        temperature=30.0 + index * 0.2,
        cell_voltages=[3.9, 3.95, 4.0],
        additional_data={},
        quality_flags={},
    )


async def _seed(device_id: str) -> dict:
    """Register a device with telemetry, SoH history and anomaly episodes."""
    async with SessionLocal() as session:
        await DeviceRepository(session).create(
            device_id=device_id,
            name="report api test",
            cell_count=3,
            sampling_frequency_hz=1.0,
            chemistry="li_ion",
            status="active",
        )
        await TelemetryRepository(session).insert_batch(
            [_sample(device_id, index) for index in range(5)]
        )
        await CycleRepository(session).insert_predictions(
            [
                {
                    "device_id": device_id,
                    "cycle_id": None,
                    "predicted_soh": 96.5,
                    "measured_soh": 96.0,
                    "model_version": "soh_window_all@1",
                    "feature_schema_version": "window-v1",
                    "input_quality_status": "ok",
                },
                {
                    "device_id": device_id,
                    "cycle_id": None,
                    "predicted_soh": 94.0,
                    "measured_soh": None,
                    "model_version": "soh_window_all@1",
                    "feature_schema_version": "window-v1",
                    "input_quality_status": "ok",
                },
            ]
        )

        repository = AnomalyRepository(session)
        open_event = await repository.open_or_extend(
            {
                "device_id": device_id,
                "anomaly_type": "cell_voltage_out_of_range",
                "affected_cell": 1,
                "severity": "critical",
                "detected_at": BASE,
                "last_detected_at": BASE + timedelta(seconds=30),
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
        await repository.add_action(
            open_event.id, action_type="acknowledge", actor="operator", note="seen"
        )
        resolved = await repository.open_or_extend(
            {
                "device_id": device_id,
                "anomaly_type": "temperature_out_of_range",
                "affected_cell": None,
                "severity": "warning",
                "detected_at": BASE + timedelta(hours=1),
                "last_detected_at": BASE + timedelta(hours=1),
                "duration_seconds": 0.0,
                "observed_value": 55.0,
                "expected_value": 45.0,
                "threshold": 45.0,
                "diagnostic_evidence": {"limit": "max"},
                "likely_causes": [],
                "recommended_actions": [],
                "rule_version": "rules-v1",
            }
        )
        await repository.close_event(resolved, BASE + timedelta(hours=2))
        await session.commit()
        return {"open": open_event.id, "resolved": resolved.id}


@pytest.fixture(scope="module")
def seeded(client: TestClient, require_db) -> tuple[str, dict]:
    import asyncio

    device_id = f"REPORT_{uuid4().hex[:8]}"
    ids = asyncio.run(_seed(device_id))
    return device_id, ids


def test_create_report_returns_stored_snapshot(client, seeded) -> None:
    device_id, _ = seeded
    created = client.post("/api/v1/reports", json={"device_id": device_id})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "ready"
    assert body["format_version"] == "report-v2"
    assert body["device_id"] == device_id
    assert body["payload"]["device"]["device_id"] == device_id
    assert body["payload"]["device"]["scope"] == "device"

    report_id = body["id"]
    fetched = client.get(f"/api/v1/reports/{report_id}")
    assert fetched.status_code == 200
    assert fetched.json()["payload"] == body["payload"]


def test_report_includes_enabled_sections(client, seeded) -> None:
    device_id, _ = seeded
    body = client.post("/api/v1/reports", json={"device_id": device_id}).json()
    payload = body["payload"]
    assert payload["included_sections"] == ["telemetry", "soh", "anomalies", "actions"]
    assert payload["telemetry"]["sample_count"] == 5
    assert payload["soh"]["count"] == 2
    assert payload["soh"]["latest_predicted_soh"] == 94.0
    assert payload["anomalies"]["total"] == 2
    assert payload["anomalies"]["unresolved_count"] == 1
    # The acknowledge and the detector's close both append to the audit trail.
    assert payload["actions"]["count"] == 2
    assert body["row_count"] == 2


def test_report_respects_section_toggles(client, seeded) -> None:
    device_id, _ = seeded
    body = client.post(
        "/api/v1/reports",
        json={
            "device_id": device_id,
            "sections": {
                "telemetry": False,
                "soh": False,
                "anomalies": True,
                "actions": False,
            },
        },
    ).json()
    payload = body["payload"]
    assert payload["included_sections"] == ["anomalies"]
    assert "telemetry" not in payload
    assert "soh" not in payload
    assert "actions" not in payload
    assert payload["anomalies"]["total"] == 2


def test_report_filters_narrow_the_anomaly_section(client, seeded) -> None:
    device_id, _ = seeded
    body = client.post(
        "/api/v1/reports",
        json={"device_id": device_id, "severity": "critical"},
    ).json()
    anomalies = body["payload"]["anomalies"]
    assert anomalies["list_count"] == 1
    assert anomalies["events"][0]["severity"] == "critical"
    assert body["payload"]["filters"]["severity"] == "critical"


def test_fleet_report_without_device(client, seeded) -> None:
    body = client.post("/api/v1/reports", json={}).json()
    assert body["device_id"] is None
    assert body["payload"]["device"]["scope"] == "fleet"
    assert body["payload"]["anomalies"]["total"] >= 2


def test_download_json_returns_payload(client, seeded) -> None:
    device_id, _ = seeded
    report_id = client.post("/api/v1/reports", json={"device_id": device_id}).json()["id"]
    response = client.get(
        f"/api/v1/reports/{report_id}/download", params={"format": "json"}
    )
    assert response.status_code == 200
    assert response.headers["content-disposition"].endswith(f'report-{report_id}.json"')
    assert response.json()["included_sections"]


def test_download_csv_projects_primary_table(client, seeded) -> None:
    device_id, _ = seeded
    report_id = client.post("/api/v1/reports", json={"device_id": device_id}).json()["id"]
    response = client.get(
        f"/api/v1/reports/{report_id}/download", params={"format": "csv"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    lines = response.text.strip().splitlines()
    header = lines[0].split(",")
    assert "anomaly_type" in header
    assert "severity" in header
    assert len(lines) == 3  # header + two events


def test_create_report_unknown_device_is_404(client, require_db) -> None:
    response = client.post("/api/v1/reports", json={"device_id": "NO_SUCH_DEVICE"})
    assert response.status_code == 404


def test_create_report_rejects_inverted_window(client, seeded) -> None:
    device_id, _ = seeded
    response = client.post(
        "/api/v1/reports",
        json={
            "device_id": device_id,
            "from": "2026-10-18T00:00:00Z",
            "to": "2026-10-17T00:00:00Z",
        },
    )
    assert response.status_code == 422


def test_download_rejects_unknown_format(client, seeded) -> None:
    device_id, _ = seeded
    report_id = client.post("/api/v1/reports", json={"device_id": device_id}).json()["id"]
    response = client.get(
        f"/api/v1/reports/{report_id}/download", params={"format": "pdf"}
    )
    assert response.status_code == 400


def test_get_unknown_report_is_404(client, require_db) -> None:
    response = client.get("/api/v1/reports/999999999")
    assert response.status_code == 404


def test_list_reports_includes_created(client, seeded) -> None:
    device_id, _ = seeded
    body = client.get("/api/v1/reports", params={"device_id": device_id}).json()
    assert body["total"] >= 1
    assert all(item["device_id"] == device_id for item in body["items"])
    assert all("payload" not in item for item in body["items"])


def test_filtered_report_counts_match_its_events_and_actions(client, seeded):
    device_id, _ = seeded
    payload = client.post('/api/v1/reports', json={
        'device_id': device_id, 'severity': 'critical',
    }).json()['payload']
    assert payload['anomalies']['total'] == 1
    assert dict(payload['anomalies']['by_severity']) == {'critical': 1}
    assert all(set(b['by_severity']) <= {'critical'} for b in payload['anomalies']['hourly'])
    event_ids = {e['id'] for e in payload['anomalies']['events']}
    assert all(a['anomaly_id'] in event_ids for a in payload['actions']['items'])


async def test_report_does_not_truncate_prediction_history_at_1000(require_db):
    from app.schemas.report import ReportCreate
    from app.services.report_service import ReportService
    device_id = f'REPORT_LONG_{uuid4().hex[:8]}'
    async with SessionLocal() as session:
        device = await DeviceRepository(session).create(
            device_id=device_id, name='long report', cell_count=1,
            sampling_frequency_hz=1, status='active',
        )
        await CycleRepository(session).insert_predictions([
            {'device_id': device_id, 'cycle_id': None, 'predicted_soh': 90.,
             'measured_soh': None, 'model_version': 'test',
             'feature_schema_version': 'window-v1', 'input_quality_status': 'ok'}
            for _ in range(1002)
        ])
        payload = await ReportService(session).build(ReportCreate(device_id=device_id), device)
        assert payload['soh']['count'] == 1002
        assert len(payload['soh']['history']) == 1002
        await session.rollback()


def test_report_can_exclude_anomalies_without_reintroducing_them(client, seeded):
    device_id, _ = seeded
    payload = client.post('/api/v1/reports', json={
        'device_id': device_id,
        'sections': {'telemetry': True, 'soh': False, 'anomalies': False, 'actions': False},
    }).json()['payload']
    assert payload['included_sections'] == ['telemetry']
    assert 'anomalies' not in payload
    assert 'actions' not in payload
    assert 'soh' not in payload


def test_mixed_naive_and_aware_report_dates_do_not_raise_server_error(client, seeded):
    device_id, _ = seeded
    response = client.post('/api/v1/reports', json={
        'device_id': device_id,
        'from': '2026-10-17T00:00:00', 'to': '2026-10-18T00:00:00Z',
    })
    assert response.status_code == 201


def test_oversized_synchronous_report_is_explicitly_rejected():
    from app.services.report_service import _check_report_size
    from fastapi import HTTPException
    _check_report_size(10000)
    with pytest.raises(HTTPException) as info:
        _check_report_size(10001)
    assert info.value.status_code == 422
    assert 'narrow' in info.value.detail
