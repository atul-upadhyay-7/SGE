"""API tests for device alerts (Phase 5).

Alerts are pure derived state: every rule is evaluated against the
persisted telemetry and SoH tables, so the tests seed those tables and
check the evaluator's decision. The monitor loop itself is not started in
tests (``BMS_MONITOR_ENABLED=false`` in conftest), so there is no background
task racing the per-test event loop.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.db.models import BatteryCycle, Device, SohPrediction, TelemetrySample
from app.db.models import Base
from app.services.alert_service import (
    RULE_CELL_IMBALANCE,
    RULE_DEVICE_STALE,
    RULE_SOH_BELOW_RETIREMENT,
    RULE_TEMPERATURE_OUT_OF_RANGE,
)


def _reset_database() -> None:
    from app.db.database import engine

    async def drop_and_create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(drop_and_create())


def _seed_device(device_id: str = "ALERT_DEV") -> None:
    async def seed():
        async with SessionLocal() as session:
            session.add(
                Device(device_id=device_id, name="Alert test pack", cell_count=3)
            )
            await session.commit()

    asyncio.run(seed())


def _seed_sample(
    device_id: str,
    *,
    measured_at: datetime | None = None,
    temperature: float = 25.0,
    quality_flags: dict | None = None,
    cells: list[float] | None = None,
) -> None:
    async def seed():
        async with SessionLocal() as session:
            session.add(
                TelemetrySample(
                    device_id=device_id,
                    message_id=f"m-{measured_at.timestamp() if measured_at else 'now'}-{temperature}",
                    measured_at=measured_at or datetime.now(timezone.utc),
                    pack_voltage=12.0,
                    current=-1.6,
                    temperature=temperature,
                    cell_voltages=cells or [3.80, 3.81, 3.82],
                    quality_flags=quality_flags,
                )
            )
            await session.commit()

    asyncio.run(seed())


def _seed_prediction(device_id: str, predicted_soh: float) -> None:
    async def seed():
        async with SessionLocal() as session:
            session.add(
                SohPrediction(
                    device_id=device_id,
                    predicted_soh=predicted_soh,
                    model_version="test-version",
                    feature_schema_version="window-v1",
                    input_quality_status="ok",
                )
            )
            await session.commit()

    asyncio.run(seed())


def test_evaluate_opens_temperature_and_imbalance_alerts(client, require_db):
    _reset_database()
    _seed_device("ALERT_T")
    settings = get_settings()
    _seed_sample(
        "ALERT_T",
        temperature=settings.alert_temperature_max_c + 10,
        quality_flags={"cell_imbalance_v": 0.15, "cell_imbalance_high": True},
    )

    response = client.post("/api/v1/devices/ALERT_T/alerts/evaluate")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["device_id"] == "ALERT_T"
    assert body["opened"] == 2
    assert body["resolved"] == 0
    assert sorted(body["firing"]) == sorted(
        [RULE_TEMPERATURE_OUT_OF_RANGE, RULE_CELL_IMBALANCE]
    )

    listing = client.get("/api/v1/alerts", params={"device_id": "ALERT_T"})
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert listing.json()["total"] == 2
    assert {item["rule_code"] for item in items} == set(body["firing"])
    assert all(item["status"] == "active" for item in items)


def test_evaluate_is_idempotent_while_condition_persists(client, require_db):
    _reset_database()
    _seed_device("ALERT_IDEM")
    settings = get_settings()
    _seed_sample("ALERT_IDEM", temperature=settings.alert_temperature_max_c + 5)

    first = client.post("/api/v1/devices/ALERT_IDEM/alerts/evaluate").json()
    second = client.post("/api/v1/devices/ALERT_IDEM/alerts/evaluate").json()

    assert first["opened"] == 1
    assert second["opened"] == 0
    assert second["updated"] == 1

    listing = client.get("/api/v1/alerts", params={"device_id": "ALERT_IDEM"})
    assert listing.json()["total"] == 1, "a firing rule must not stack rows"


def test_evaluate_resolves_alert_when_condition_clears(client, require_db):
    _reset_database()
    _seed_device("ALERT_CLEAR")
    settings = get_settings()
    _seed_sample("ALERT_CLEAR", temperature=settings.alert_temperature_max_c + 5)
    client.post("/api/v1/devices/ALERT_CLEAR/alerts/evaluate")

    # A fresh sample back inside the range clears the condition.
    _seed_sample("ALERT_CLEAR", temperature=25.0)
    result = client.post("/api/v1/devices/ALERT_CLEAR/alerts/evaluate").json()
    assert result["resolved"] == 1
    assert result["firing"] == []

    listing = client.get(
        "/api/v1/alerts",
        params={"device_id": "ALERT_CLEAR", "status": "resolved"},
    )
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["resolved_at"] is not None


def test_soh_retirement_alert(client, require_db):
    _reset_database()
    _seed_device("ALERT_SOH")
    _seed_sample("ALERT_SOH")
    _seed_prediction("ALERT_SOH", predicted_soh=75.0)

    result = client.post("/api/v1/devices/ALERT_SOH/alerts/evaluate").json()
    assert RULE_SOH_BELOW_RETIREMENT in result["firing"]

    alerts = client.get(
        "/api/v1/alerts",
        params={"device_id": "ALERT_SOH", "severity": "critical"},
    ).json()
    assert alerts["total"] == 1
    assert alerts["items"][0]["details"]["predicted_soh"] == 75.0


def test_stale_device_alert(client, require_db):
    _reset_database()
    _seed_device("ALERT_STALE")
    settings = get_settings()
    _seed_sample(
        "ALERT_STALE",
        measured_at=datetime.now(timezone.utc)
        - timedelta(seconds=settings.device_stale_after_s + 60),
    )

    result = client.post("/api/v1/devices/ALERT_STALE/alerts/evaluate").json()
    assert result["firing"] == [RULE_DEVICE_STALE]


def test_device_with_no_samples_is_not_stale(client, require_db):
    _reset_database()
    _seed_device("ALERT_NEW")
    result = client.post("/api/v1/devices/ALERT_NEW/alerts/evaluate").json()
    assert result["firing"] == [], "a new device has nothing to be stale about"


def test_ack_alert(client, require_db):
    _reset_database()
    _seed_device("ALERT_ACK")
    settings = get_settings()
    _seed_sample("ALERT_ACK", temperature=settings.alert_temperature_max_c + 5)
    client.post("/api/v1/devices/ALERT_ACK/alerts/evaluate")

    listing = client.get("/api/v1/alerts", params={"device_id": "ALERT_ACK"}).json()
    alert_id = listing["items"][0]["id"]

    ack = client.post(
        f"/api/v1/alerts/{alert_id}/ack", json={"acked_by": "operator-1"}
    )
    assert ack.status_code == 200
    body = ack.json()
    assert body["status"] == "acked"
    assert body["acked_by"] == "operator-1"

    # A still-firing acked alert stays acked and does not reopen.
    result = client.post("/api/v1/devices/ALERT_ACK/alerts/evaluate").json()
    assert result["opened"] == 0
    assert result["updated"] == 1
    after = client.get(f"/api/v1/alerts/{alert_id}").json()
    assert after["status"] == "acked"


def test_ack_missing_alert_is_404(client, require_db):
    _reset_database()
    response = client.post("/api/v1/alerts/999999/ack", json={"acked_by": "op"})
    assert response.status_code == 404


def test_unknown_device_evaluate_is_404(client, require_db):
    _reset_database()
    response = client.post("/api/v1/devices/NOPE/alerts/evaluate")
    assert response.status_code == 404


def test_list_alerts_pagination(client, require_db):
    _reset_database()
    settings = get_settings()
    for index in range(3):
        device_id = f"ALERT_PAGE{index}"
        _seed_device(device_id)
        # The rule is `temperature > trained_max`, so start strictly above.
        _seed_sample(
            device_id, temperature=settings.alert_temperature_max_c + 1 + index
        )

    for index in range(3):
        client.post(f"/api/v1/devices/ALERT_PAGE{index}/alerts/evaluate")

    page = client.get("/api/v1/alerts", params={"limit": 2, "offset": 0}).json()
    assert page["count"] == 2
    assert page["total"] == 3

    page2 = client.get("/api/v1/alerts", params={"limit": 2, "offset": 2}).json()
    assert page2["count"] == 1
    assert {i["id"] for i in page["items"]}.isdisjoint({i["id"] for i in page2["items"]})


def test_alert_rules_match_offline_thresholds(client, require_db):
    """The temperature rule must use the trained-range bound, not a guess."""
    settings = get_settings()
    assert settings.alert_soh_retire_pct == 80.0
    assert abs(settings.alert_temperature_max_c - 63.0242156686) < 1e-6
