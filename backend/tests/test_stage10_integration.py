"""Stage 10: cross-cutting security and reliability invariants.

Phase-level suites cover each feature; these tests assert the guarantees that
only hold when the features meet:

* **No secret leaks anywhere.** A registered device's credential must not appear
  (nor its digest) in *any* read surface — device, alert, anomaly or report.
* **No invented data.** Diagnoses, corrective actions, SoH and recurrence come
  from stored rows / documented rules (PRD §8), never synthesized to fill a
  panel; an empty window yields honest zeros.
* **Reliability isolation.** A detector or writer failure never loses stored
  telemetry and never kills the ingestion loop (CR §10).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.repositories.anomaly_repository import AnomalyRepository
from app.repositories.cycle_repository import CycleRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.telemetry_repository import TelemetryRepository
from app.schemas.telemetry import ValidatedSample
from app.services.anomaly_rules import RULES_VERSION
from app.core.security import hash_credential

UTC = timezone.utc
BASE = datetime(2026, 10, 18, 6, 0, tzinfo=UTC)
DEVICE_CREDENTIAL_HEADER = "X-Device-Credential"


def _sample(device_id: str, index: int) -> ValidatedSample:
    return ValidatedSample(
        device_id=device_id,
        message_id=f"msg-{index}",
        measured_at=BASE + timedelta(seconds=index),
        pack_voltage=48.0,
        current=5.0,
        temperature=30.0,
        cell_voltages=[3.9, 3.95, 4.0],
        additional_data={},
        quality_flags={},
    )


async def _seed(device_id: str, secret: str) -> int:
    """A protected device with telemetry, a prediction and a diagnosed anomaly."""
    async with SessionLocal() as session:
        await DeviceRepository(session).create(
            device_id=device_id,
            name="stage10 device",
            cell_count=3,
            sampling_frequency_hz=1.0,
            chemistry="li_ion",
            status="active",
            credential_ref=hash_credential(secret),
        )
        await TelemetryRepository(session).insert_batch(
            [_sample(device_id, i) for i in range(3)]
        )
        await CycleRepository(session).insert_predictions(
            [
                {
                    "device_id": device_id,
                    "cycle_id": None,
                    "predicted_soh": 93.25,
                    "measured_soh": 93.0,
                    "model_version": "soh_window_all@1",
                    "feature_schema_version": "window-v1",
                    "input_quality_status": "ok",
                }
            ]
        )
        event = await AnomalyRepository(session).open_or_extend(
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
                "rule_version": RULES_VERSION,
            }
        )
        await session.commit()
        return event.id


@pytest.fixture(scope="module")
def seeded(client: TestClient, require_db) -> tuple[str, str, int]:
    import asyncio

    device_id = f"STAGE10_{uuid4().hex[:8]}"
    secret = f"secret-{uuid4().hex[:12]}"
    anomaly_id = asyncio.run(_seed(device_id, secret))
    return device_id, secret, anomaly_id


def _all_read_payloads(client: TestClient, device_id: str, anomaly_id: int) -> list[str]:
    """Text of every CR read surface for a device, to scan for leaks."""
    paths = [
        "/api/v1/devices",
        f"/api/v1/devices/{device_id}",
        f"/api/v1/telemetry/latest/{device_id}",
        f"/api/v1/telemetry/summary/{device_id}",
        "/api/v1/anomalies",
        f"/api/v1/anomalies/{anomaly_id}",
        "/api/v1/cycles",
        f"/api/v1/predictions/latest/{device_id}",
    ]
    texts = [client.get(path).text for path in paths]
    report = client.post("/api/v1/reports", json={"device_id": device_id}).json()
    texts.append(client.get(f"/api/v1/reports/{report['id']}").text)
    return texts


def test_no_credential_or_digest_leaks_anywhere(client, seeded) -> None:
    device_id, secret, anomaly_id = seeded
    digest = hash_credential(secret)
    for text in _all_read_payloads(client, device_id, anomaly_id):
        assert secret not in text
        assert digest not in text
        assert "credential_ref" not in text


def test_anomaly_exposes_rule_provenance_not_a_guess(client, seeded) -> None:
    _, _, anomaly_id = seeded
    body = client.get(f"/api/v1/anomalies/{anomaly_id}").json()
    # The diagnosis is the rule's, with its evidence and version.
    assert body["rule_version"] == RULES_VERSION
    assert body["diagnostic_evidence"]
    assert body["likely_causes"]
    assert body["recommended_actions"]
    # A cause is a hypothesis, never asserted as a confirmed failed component.
    for cause in body["likely_causes"]:
        assert cause.get("confirmed") is False


def test_report_soh_is_the_stored_prediction(client, seeded) -> None:
    device_id, _, _ = seeded
    report = client.post("/api/v1/reports", json={"device_id": device_id}).json()
    soh = report["payload"]["soh"]
    assert soh["count"] == 1
    assert soh["latest_predicted_soh"] == 93.25
    # The report never invents a diagnosis its events do not carry.
    for event in report["payload"]["anomalies"]["events"]:
        assert event["rule_version"] == RULES_VERSION


def test_report_over_an_empty_window_is_honest(client, require_db) -> None:
    """A window with no data yields zeros and nulls, not fabricated numbers."""
    device_id = f"STAGE10_EMPTY_{uuid4().hex[:8]}"
    import asyncio

    async def register() -> None:
        async with SessionLocal() as session:
            await DeviceRepository(session).create(
                device_id=device_id,
                name="empty window",
                cell_count=3,
                chemistry="li_ion",
                status="active",
            )
            await session.commit()

    asyncio.run(register())
    report = client.post(
        "/api/v1/reports",
        json={
            "device_id": device_id,
            "from": "2020-01-01T00:00:00Z",
            "to": "2020-01-02T00:00:00Z",
        },
    ).json()
    payload = report["payload"]
    assert payload["telemetry"]["sample_count"] == 0
    assert payload["telemetry"]["pack_voltage"]["mean"] is None
    assert payload["soh"]["count"] == 0
    assert payload["soh"]["latest_predicted_soh"] is None
    assert payload["anomalies"]["total"] == 0
    assert payload["anomalies"]["events"] == []


async def test_detector_failure_does_not_drop_stored_samples(require_db, monkeypatch):
    """A detector fault is isolated: samples commit before detection runs (CR §10)."""
    from app.services.device_cache import DeviceCache
    from app.services.ingestion_service import IngestionService

    device_id = f"STAGE10_ISO_{uuid4().hex[:8]}"
    async with SessionLocal() as session:
        await DeviceRepository(session).create(
            device_id=device_id,
            name="isolation",
            cell_count=3,
            chemistry="li_ion",
            status="active",
        )
        await session.commit()

    async def _explode(samples):
        raise RuntimeError("detector exploded")

    service = IngestionService(
        SessionLocal, DeviceCache(), get_settings(), events=None
    )
    monkeypatch.setattr(service, "_detect_anomalies", _explode)

    # _flush commits the samples, then calls the (exploding) detector.
    await service._flush([_sample(device_id, 0), _sample(device_id, 1)])

    async with SessionLocal() as session:
        count = await TelemetryRepository(session).count_samples(device_id)
    assert count == 2
