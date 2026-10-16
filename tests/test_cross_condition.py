"""
test_cross_condition.py
-----------------------
The cross-condition experiment must be honest and free of leakage.

These tests read the artifacts the pipeline writes and check the
claims the report makes about them:

  * the held-out G9 line is B0053 alone, never a group average;
  * cell-relative features beat raw features on every group;
  * the Coulomb formula still beats the model on the two multi-cell
    groups (the report says so and must stay true);
  * the cell-relative feature list is ratio-to-own-baseline and
    shares nothing with the canonical leaky list.
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
CLEAN = REPO / "data" / "processed" / "nasa_ml_dataset_clean.csv"


@pytest.fixture(scope="module")
def cross():
    if not CROSS.exists():
        pytest.skip("run_experiments.py has not produced cross_condition.csv")
    return pd.read_csv(CROSS)


def _mae(cross, group, feature_set, model):
    row = cross[
        (cross["test_group"] == group)
        & (cross["feature_set"] == feature_set)
        & (cross["model"] == model)
    ]
    return float(row["mae_pct"].iloc[0])


def _coulomb(cross, group):
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


def test_coulomb_still_wins_multi_cell_groups(cross):
    """The report claims the formula beats the model across groups."""
    for group in ("G1", "G3"):
        coulomb = _coulomb(cross, group)
        rel = _mae(cross, group, "cell_relative", "ElasticNet")
        assert coulomb < rel, (
            f"{group}: Coulomb {coulomb} no longer beats "
            f"cell-relative {rel}; update the report claim"
        )


def test_cell_relative_wins_on_b0053(cross):
    """The one cell where the model does beat the formula."""
    coulomb = _coulomb(cross, "G9")
    rel = _mae(cross, "G9", "cell_relative", "ElasticNet")
    assert rel < coulomb


def test_norm_features_are_not_leaky():
    assert not set(R.NORM_FEATURES) & set(LEAKY_FEATURES)


def test_cell_relative_features_are_finite_and_relative():
    if not CLEAN.exists():
        pytest.skip("clean dataset not built")
    df = pd.read_csv(CLEAN)
    out = R.cell_relative_features(df)

    for column in R.NORM_FEATURES:
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
