"""Validation harness + ``GET /validation/summary`` tests.

The metric/refusal tests exercise ``scripts/validate_real_data.py``
with an injected predictor (same contract as the live estimator), so
they need no model file and no database. The endpoint tests override
the settings dependency to point at a tmp report file.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

import features  # noqa: E402

API = "/api/v1"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "validate_real_data", REPO_ROOT / "scripts" / "validate_real_data.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record(cell: str, index: int, actual: float = 90.0) -> dict:
    return {
        "cell_id": cell,
        "discharge_index": index,
        "vw_time_s": 1000.0,
        "vw_capacity_ah": 1.0,
        "vw_voltage_mean": 3.7,
        "vw_dqdv_peak_v": 3.5,
        "vw_dqdv_peak_height": 5.0,
        "current_mean": 2.0,
        "actual_soh_pct": actual,
    }


def test_metrics_and_refusals_with_injected_predictor() -> None:
    script = _load_script()

    def fake_predict(window, early):  # noqa: ANN001, ANN202
        # Scored rows are identified by their window time in this fixture:
        # the caller passes the row itself as `window`.
        return 91.0

    records = [_record("A", i) for i in range(1, 7)]  # 5 reference + 1 scored
    report = script.build_report(
        records, source="test", dataset_name="fixture.csv", predict_fn=fake_predict
    )
    summary = report["summary"]
    assert summary["reference_rows"] == 5
    assert summary["scored_rows"] == 1
    assert summary["refused_rows"] == 0
    assert summary["mae_pct"] == 1.0
    assert summary["rmse_pct"] == 1.0
    assert summary["bias_pct"] == 1.0
    assert report["model"]["algorithm"] == "XGBoost"
    assert report["model"]["live"] is True


def test_out_of_range_and_insufficient_reference_are_refused() -> None:
    script = _load_script()

    def fake_predict(window, early):  # noqa: ANN001, ANN202
        return features.OUT_OF_TRAINED_RANGE

    records = [_record("A", i) for i in range(1, 8)]  # 5 ref + 2 refused
    records += [_record("B", i) for i in range(1, 3)]  # < 3 rows: refused
    rows = script.validate_records(records, predict_fn=fake_predict)
    summary = script.compute_metrics(rows)
    assert summary["scored_rows"] == 0
    assert summary["mae_pct"] is None
    assert summary["refusals_by_reason"] == {
        "out_of_trained_range": 2,
        "insufficient_reference": 2,
    }


def test_window_range_error_is_refused() -> None:
    script = _load_script()

    def fake_predict(window, early):  # noqa: ANN001, ANN202
        raise features.WindowRangeError("outside trained window")

    records = [_record("A", i) for i in range(1, 7)]
    rows = script.validate_records(records, predict_fn=fake_predict)
    summary = script.compute_metrics(rows)
    assert summary["refusals_by_reason"] == {"window_range": 1}


def _report_dict() -> dict:
    return {
        "schema_version": 1,
        "generated_at": "2026-10-23T00:00:00+00:00",
        "source": "lab_sample_nasa",
        "dataset": "sample_input.csv",
        "model": {"name": "soh_window_all", "artifact": "models/soh_window_all.ubj",
                  "algorithm": "XGBoost", "live": True},
        "summary": {"total_rows": 6, "reference_rows": 5, "scored_rows": 1,
                    "refused_rows": 0, "mae_pct": 1.0, "rmse_pct": 1.0,
                    "bias_pct": 1.0},
        "offline_benchmark": None,
        "notes": [],
        "rows": [],
    }


def test_summary_serves_latest_report(tmp_path: Path) -> None:
    """Endpoint function called directly: other tests reload app.main,
    which makes dependency-override identity unreliable across the
    session client — the handler itself is the unit under test here."""
    import asyncio

    from app.api import validation
    from app.core.config import Settings
    from fastapi import HTTPException

    path = tmp_path / "latest_validation.json"
    path.write_text(json.dumps(_report_dict()), encoding="utf-8")
    body = asyncio.run(
        validation.validation_summary(
            settings=Settings(validation_report_path=str(path))
        )
    )
    assert body["summary"]["mae_pct"] == 1.0
    assert body["summary"]["scored_rows"] == 1
    assert body["model"]["algorithm"] == "XGBoost"

    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            validation.validation_summary(
                settings=Settings(validation_report_path=str(missing))
            )
        )
    assert excinfo.value.status_code == 404
    assert "validate_real_data" in excinfo.value.detail


def test_summary_route_is_mounted(client: TestClient) -> None:
    """Routing smoke test: 200 with a well-shaped report when a run
    exists in the repo, 404 with run instructions when it does not."""
    r = client.get(f"{API}/validation/summary")
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        body = r.json()
        assert body["model"]["algorithm"] == "XGBoost"
        assert "mae_pct" in body["summary"]
    else:
        assert "validate_real_data" in r.json()["detail"]
