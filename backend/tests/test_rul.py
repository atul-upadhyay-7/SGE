"""Tests for RUL / end-of-life estimation (Phase 7).

The estimator is a thin, faithful wrapper over the offline
``src/rul_trajectory.py``: these tests pin the *contract* (statuses, units,
band ordering, the retired short-circuit) and the API surface, rather than
re-deriving the offline numbers.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from app.ml.rul_estimator import (
    DEFAULT_REFERENCE_PATH,
    STATUS_NO_HISTORY,
    STATUS_OK,
    STATUS_RETIRED,
    clear_cache,
    estimate_rul,
)

# A plausible descending SoH history, most recent last.
DESCENDING = [100.0, 99.5, 99.0, 98.5, 98.0, 97.5, 97.0, 96.5, 96.0, 95.0]


# --------------------------------------------------------------------------
# estimator unit tests
# --------------------------------------------------------------------------


def test_reference_artifact_is_present() -> None:
    assert DEFAULT_REFERENCE_PATH.exists()


def test_healthy_history_yields_a_positive_estimate() -> None:
    estimate = estimate_rul(DESCENDING)
    assert estimate.status == STATUS_OK
    assert estimate.rul_discharges is not None
    assert estimate.rul_discharges > 0
    assert estimate.reference_count > 0
    assert estimate.history_count == len(DESCENDING)


def test_band_brackets_the_point_estimate() -> None:
    estimate = estimate_rul(DESCENDING)
    assert estimate.p10_discharges is not None
    assert estimate.p90_discharges is not None
    assert estimate.p10_discharges <= estimate.rul_discharges
    assert estimate.rul_discharges <= estimate.p90_discharges


def test_lower_soh_means_less_remaining_life() -> None:
    healthier = estimate_rul([99.5] * 10)
    weaker = estimate_rul([85.0] * 10)
    assert healthier.rul_discharges > weaker.rul_discharges


def test_retired_history_reports_zero_not_a_negative() -> None:
    estimate = estimate_rul([79.0] * 10)
    assert estimate.status == STATUS_RETIRED
    assert estimate.rul_discharges == 0


def test_empty_history_is_reported_as_no_history() -> None:
    estimate = estimate_rul([])
    assert estimate.status == STATUS_NO_HISTORY
    assert estimate.rul_discharges is None


def test_point_estimate_matches_the_offline_estimator() -> None:
    """The wrapper must not disagree with the offline median."""
    from rul_trajectory import TrajectoryRul

    offline = TrajectoryRul.load(DEFAULT_REFERENCE_PATH)
    expected = offline.remaining(list(DESCENDING))
    assert estimate_rul(DESCENDING).rul_discharges == expected


def test_long_history_uses_the_configured_window() -> None:
    """Only the newest values matter; an old healthy stretch must not inflate RUL."""
    short = estimate_rul([88.0] * 10)
    # Same recent tail, padded with a long-ago healthy stretch.
    padded = estimate_rul([100.0] * 50 + [88.0] * 10)
    assert padded.rul_discharges == short.rul_discharges


def test_cache_can_be_cleared() -> None:
    estimate_rul(DESCENDING)
    clear_cache()
    # Still works after the cache is dropped (reloads from disk).
    assert estimate_rul(DESCENDING).status == STATUS_OK


# --------------------------------------------------------------------------
# API tests
# --------------------------------------------------------------------------


def _seed_device(device_id: str) -> None:
    from app.db.database import SessionLocal
    from app.db.models import Device

    async def seed():
        async with SessionLocal() as session:
            session.add(
                Device(device_id=device_id, name="RUL pack", cell_count=3)
            )
            await session.commit()

    asyncio.run(seed())


def _seed_predictions(device_id: str, values: list[float]) -> None:
    from datetime import datetime, timedelta, timezone

    from app.db.database import SessionLocal
    from app.db.models import SohPrediction

    async def seed():
        async with SessionLocal() as session:
            base = datetime(2026, 10, 1, tzinfo=timezone.utc)
            for index, value in enumerate(values):
                session.add(
                    SohPrediction(
                        device_id=device_id,
                        predicted_soh=value,
                        model_version="test",
                        feature_schema_version="window-v1",
                        predicted_at=base + timedelta(hours=index),
                    )
                )
            await session.commit()

    asyncio.run(seed())


def test_rul_endpoint_unknown_device_returns_404(client, require_db) -> None:
    response = client.get(f"/api/v1/devices/NOPE_{uuid4().hex[:8]}/rul")
    assert response.status_code == 404


def test_rul_endpoint_without_predictions_reports_no_predictions(
    client, require_db
) -> None:
    device_id = f"RUL_EMPTY_{uuid4().hex[:8]}"
    _seed_device(device_id)
    response = client.get(f"/api/v1/devices/{device_id}/rul")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "no_predictions"
    assert body["rul_discharges"] is None
    assert body["history"] == []


def test_rul_endpoint_estimates_from_predictions(client, require_db) -> None:
    device_id = f"RUL_OK_{uuid4().hex[:8]}"
    _seed_device(device_id)
    _seed_predictions(device_id, DESCENDING)

    response = client.get(f"/api/v1/devices/{device_id}/rul")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status"] == "ok"
    assert body["rul_discharges"] > 0
    assert body["p10_discharges"] <= body["rul_discharges"] <= body["p90_discharges"]
    assert body["total_predictions"] == len(DESCENDING)
    assert body["basis"] == "reference_trajectory_matching"
    assert len(body["history"]) == len(DESCENDING)


def test_rul_endpoint_retired_device(client, require_db) -> None:
    device_id = f"RUL_RET_{uuid4().hex[:8]}"
    _seed_device(device_id)
    _seed_predictions(device_id, [79.0] * 10)

    response = client.get(f"/api/v1/devices/{device_id}/rul")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "retired"
    assert body["rul_discharges"] == 0


def test_rul_endpoint_reflects_new_predictions(client, require_db) -> None:
    """RUL is derived, so a re-aggregation (new predictions) changes it."""
    device_id = f"RUL_LIVE_{uuid4().hex[:8]}"
    _seed_device(device_id)
    _seed_predictions(device_id, [98.0] * 10)
    healthy = client.get(f"/api/v1/devices/{device_id}/rul").json()["rul_discharges"]

    _seed_predictions(device_id, [85.0] * 10)
    weaker = client.get(f"/api/v1/devices/{device_id}/rul").json()["rul_discharges"]

    assert weaker < healthy
