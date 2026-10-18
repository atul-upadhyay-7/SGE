"""
test_charge_soh.py
------------------
The loader keeps discharge cycles only: ``iter_discharge_traces`` skips
every cycle whose type is not a discharge, so the ML dataset, the window
model and every document see discharges alone. This test pins that, and
pins the answer to the follow-up question — can the constant-current
charge estimate SOH?

The charge answer comes from ``soh_charge_window*.csv``
(src/charge_soh.py), a separate stage that reads charge segments without
touching the discharge path. These tests check:

  * the discharge reader drops charge cycles and the charge reader keeps
    them, counted against the raw .mat cycle list;
  * both models are scored on all thirteen held-out cells;
  * the charge window matches the discharge window on G3 but is worse on
    G1, B0053 and the random-load cells;
  * the discharge column is the same held-out task as the discharge
    sweep's trained band;
  * docs/RESULTS.md carries the section.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

PROC = REPO / "data" / "processed"
DETAIL = PROC / "soh_charge_window.csv"
COMPARISON = PROC / "soh_charge_window_comparison.csv"
DISCHARGE_SWEEP = PROC / "soh_window_train_sweep.csv"
RESULTS = REPO / "docs" / "RESULTS.md"

PROTOCOLS = (
    "leave-one-cell-out: G1",
    "leave-one-cell-out: G3",
    "train G1+G3, test B0053",
    "train G1+G3, test random-load",
)

TOL = 1e-6
LIMIT = 3.0


@pytest.fixture(scope="module")
def detail():
    if not DETAIL.exists():
        pytest.skip("charge_soh.py has not produced its CSVs")
    return pd.read_csv(DETAIL)


@pytest.fixture(scope="module")
def comparison():
    if not COMPARISON.exists():
        pytest.skip("charge_soh.py has not produced its CSVs")
    return pd.read_csv(COMPARISON)


def _cell(comparison, protocol, model):
    block = comparison[
        (comparison["protocol"] == protocol) & (comparison["model"] == model)
    ]
    return block.iloc[0]


def test_the_discharge_loader_drops_charge_and_the_charge_reader_keeps_it():
    """Count each reader against the raw cycle list so the claim is not
    taken on faith."""

    from load_data import iter_discharge_traces, resolve_raw_dir, to_scalar
    from scipy.io import loadmat

    import charge_soh

    raw = resolve_raw_dir()
    mat_path = raw / "B0005.mat"

    mat = loadmat(mat_path, squeeze_me=True, struct_as_record=False)
    cycles = np.atleast_1d(mat["B0005"].cycle)
    kinds = [
        str(to_scalar(getattr(cycle, "type", ""))).lower()
        for cycle in cycles
    ]

    n_charge = sum(1 for kind in kinds if "charge" in kind and "discharge" not in kind)
    n_discharge = sum(1 for kind in kinds if "discharge" in kind)

    assert n_charge > 0 and n_discharge > 0

    assert len(list(iter_discharge_traces(mat_path))) == n_discharge
    assert len(list(charge_soh.iter_charge_traces(mat_path))) == n_charge


def test_charge_is_scored_for_both_models_on_all_cells(detail):
    assert set(detail["model"]) == {"XGBoost", "ElasticNet", "window_formula"}

    xgb = detail[detail["model"] == "XGBoost"]

    assert set(xgb["protocol"]) == set(PROTOCOLS)
    assert xgb["test_cell"].nunique() == 13
    assert (xgb["n_cycles"] > 0).all()


def test_the_charge_window_matches_on_g3_and_loses_elsewhere(comparison):
    g3 = _cell(comparison, "leave-one-cell-out: G3", "XGBoost")
    assert g3["charge_mae_pct"] < LIMIT
    assert np.isclose(g3["charge_mae_pct"], g3["discharge_mae_pct"], atol=1.0)

    for protocol in (
        "leave-one-cell-out: G1",
        "train G1+G3, test B0053",
        "train G1+G3, test random-load",
    ):
        row = _cell(comparison, protocol, "XGBoost")
        assert row["charge_mae_pct"] > LIMIT, protocol
        assert row["charge_mae_pct"] > row["discharge_mae_pct"], protocol
        assert row["delta_mae_pct"] > 0.0


def test_the_discharge_column_is_the_discharge_sweep(comparison):
    sweep = pd.read_csv(DISCHARGE_SWEEP)
    band = sweep[sweep["window"] == "window_4.0_3.4"]

    for _, row in comparison.iterrows():

        block = band[
            (band["protocol"] == row["protocol"])
            & (band["model"] == row["model"])
        ]

        if block.empty:
            continue

        weights = block["n_cycles"].to_numpy(dtype=float)
        pooled = float(
            (block["mae_pct"].to_numpy(dtype=float) * weights).sum()
            / weights.sum()
        )

        assert np.isclose(
            row["discharge_mae_pct"], pooled, rtol=0.0, atol=TOL
        ), row["protocol"]


def test_results_document_carries_the_charge_section(comparison):
    text = RESULTS.read_text()

    assert "Can constant-current charging estimate SOH?" in text
    assert "3.8–4.0 V" in text

    b0053 = _cell(comparison, "train G1+G3, test B0053", "XGBoost")

    assert f"{b0053['charge_mae_pct']:.2f}" in text
