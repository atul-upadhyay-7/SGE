"""
test_cross_condition.py
-----------------------
The partial-discharge experiment must be honest and free of leakage.

These tests read the artifacts the pipeline writes and check the
claims the report makes about them:

  * the held-out G9 line is B0053 alone, never a group average;
  * cell-relative window features beat raw window features on every
    group;
  * the model beats the window formula (the best a BMS can compute
    from the window alone) on every held-out group;
  * the window-feature list is ratio-to-own-baseline and shares
    nothing with the canonical leaky list;
  * the reference-window sensitivity behaves: a one-discharge
    baseline is the worst case.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from features import LEAKY_FEATURES  # noqa: E402
import run_experiments as R  # noqa: E402

CROSS = REPO / "data" / "processed" / "experiments" / "cross_condition.csv"
TRANSFER = REPO / "data" / "processed" / "experiments" / "soh_partial_transfer.csv"
WINDOW = REPO / "data" / "processed" / "experiments" / "soh_partial_reference_window.csv"
PARTIAL = REPO / "data" / "processed" / "experiments" / "soh_partial_discharge.csv"
CLEAN = REPO / "data" / "processed" / "nasa_ml_dataset_clean.csv"


def _load(path):
    if not path.exists():
        pytest.skip(f"run_experiments.py has not produced {path.name}")
    return pd.read_csv(path)


@pytest.fixture(scope="module")
def cross():
    return _load(CROSS)


@pytest.fixture(scope="module")
def transfer():
    return _load(TRANSFER)


@pytest.fixture(scope="module")
def window():
    return _load(WINDOW)


def _mae(cross, group, feature_set, model):
    row = cross[
        (cross["test_group"] == group)
        & (cross["feature_set"] == feature_set)
        & (cross["model"] == model)
    ]
    return float(row["mae_pct"].iloc[0])


def _physics(cross, group):
    row = cross[
        (cross["test_group"] == group)
        & (cross["feature_set"] == "physics")
    ]
    return float(row["mae_pct"].iloc[0])


def test_g9_is_b0053_alone(cross):
    g9 = cross[cross["test_group"] == "G9"]
    assert (g9["n_cells"] == 1).all()
    assert (g9["test_cells"] == "B0053").all()
    assert (g9["n_cycles"] == 55).all()


def test_cell_relative_beats_raw_every_group(cross):
    for group in ("G1", "G3", "G9"):
        raw = _mae(cross, group, "raw", "ElasticNet")
        rel = _mae(cross, group, "cell_relative", "ElasticNet")
        assert rel < raw, f"{group}: cell-relative {rel} did not beat raw {raw}"


def test_physics_baseline_is_the_window_formula(cross):
    physics = cross[cross["feature_set"] == "physics"]
    assert (physics["method"] == "window formula").all()


def test_model_beats_window_formula_every_group(cross):
    """The report claims the model beats the window formula."""
    for group in ("G1", "G3", "G9"):
        formula = _physics(cross, group)
        rel = _mae(cross, group, "cell_relative", "ElasticNet")
        assert rel < formula, (
            f"{group}: cell-relative {rel} no longer beats "
            f"window formula {formula}; update the report claim"
        )


def test_norm_features_are_not_leaky():
    assert not set(R.WINDOW_NORM_FEATURES) & set(LEAKY_FEATURES)
    assert not set(R.NORM_FEATURES) & set(LEAKY_FEATURES)


def test_cell_relative_features_are_finite_and_relative():
    if not CLEAN.exists():
        pytest.skip("clean dataset not built")
    df = pd.read_csv(CLEAN)
    out = R.cell_relative_features(df)

    for column in R.WINDOW_NORM_FEATURES:
        assert column in out.columns
        values = out[column].to_numpy(dtype=float)
        assert np.isfinite(values).all(), f"{column} has non-finite values"

    # The ratio must actually be the raw column over the cell's own
    # first-five median, not the raw level and not a global scale.
    sdf = df.sort_values(["cell_id", "discharge_index"]).reset_index(drop=True)
    baseline = (
        sdf.groupby("cell_id")["vw_capacity_ah"]
        .transform(lambda s: s.dropna().head(5).median())
    )
    expected = (sdf["vw_capacity_ah"] / baseline).to_numpy(dtype=float)
    got = out["vw_capacity_ratio"].to_numpy(dtype=float)
    assert np.allclose(got, expected, rtol=1e-9)


def test_reference_window_parameter_changes_the_baseline():
    if not CLEAN.exists():
        pytest.skip("clean dataset not built")
    df = pd.read_csv(CLEAN)
    one = R.cell_relative_features(df, ref_window=1)
    five = R.cell_relative_features(df, ref_window=5)
    assert not np.allclose(
        one["vw_capacity_ratio"], five["vw_capacity_ratio"]
    )


def test_transfer_window_formula_matches_partial_discharge(transfer):
    """The transfer table scores the same window formula as the
    per-cell partial-discharge table."""
    partial = _load(PARTIAL)
    for cell in ("B0005", "B0006", "B0007", "B0018"):
        expected = float(
            partial.loc[
                (partial["method"] == "partial_window_formula")
                & (partial["cell_id"] == cell),
                "mae_pct",
            ].iloc[0]
        )
        row = transfer[
            (transfer["protocol"] == "leave-one-cell-out: G1")
            & (transfer["test_cell"] == cell)
            & (transfer["model"] == "window_formula")
        ]
        assert np.isclose(float(row["mae_pct"].iloc[0]), expected, rtol=1e-9)


def test_one_discharge_baseline_hurts_the_transfer(window):
    """Within a group the reference-window length barely matters, but
    the cross-condition transfer to B0053 degrades with a short
    baseline. The report says exactly that."""
    transfer = window[window["protocol"] == "train G1+G3, test B0053"]

    def mae(n, model):
        rows = transfer[
            (transfer["ref_window"] == n) & (transfer["model"] == model)
        ]
        return float(rows["mae_pct"].iloc[0])

    for model in ("ElasticNet", "XGBoost", "window_formula"):
        assert mae(1, model) > mae(5, model), (
            f"{model}: one-discharge baseline did not hurt the transfer"
        )
