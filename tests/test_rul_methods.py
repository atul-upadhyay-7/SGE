"""
test_rul_methods.py
-------------------
The RUL method comparison must match the story the report tells.

These tests read the artifacts the pipeline writes and check:

  * the fade-rate extrapolation is scored on the same held-out
    discharges as trajectory matching;
  * trajectory matching wins the mean and three of four cells, while
    the extrapolation wins exactly the fast fader B0006;
  * the leave-one-cell-out calibrated 10th/90th band does not reach
    its nominal coverage, so the report cannot claim a calibrated
    interval.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import rul_trajectory as RT  # noqa: E402

COMPARISON = REPO / "data" / "processed" / "rul_method_comparison.csv"
BAND = REPO / "data" / "processed" / "rul_band_calibration.csv"
CLEAN = REPO / "data" / "processed" / "nasa_ml_dataset_clean.csv"

CELLS = ("B0005", "B0006", "B0007", "B0018")


def _load(path):
    if not path.exists():
        pytest.skip(f"rul_trajectory.py has not produced {path.name}")
    return pd.read_csv(path)


@pytest.fixture(scope="module")
def comparison():
    return _load(COMPARISON)


@pytest.fixture(scope="module")
def band():
    return _load(BAND)


def _cell(frame, cell):
    return frame[frame["test_cell"] == cell]


def test_comparison_scores_both_methods(comparison):
    assert set(comparison["test_cell"]) == set(CELLS)
    assert {"trajectory_mae", "extrapolation_mae",
            "fleet_lifetime_baseline_mae"} <= set(comparison.columns)
    assert (comparison["window"] == RT.EXTRAP_WINDOW).all()
    assert comparison["extrapolation_mae"].notna().all()


def test_trajectory_wins_the_mean(comparison):
    """The report claims trajectory matching wins on the mean."""
    traj = float(comparison["trajectory_mae"].mean())
    extrap = float(comparison["extrapolation_mae"].mean())
    assert traj < extrap, (
        f"trajectory mean {traj} no longer beats extrapolation {extrap}; "
        "update the report claim"
    )


def test_extrapolation_wins_only_the_fast_fader(comparison):
    """The report says the extrapolation wins exactly one cell, B0006,
    and loses on B0005 and B0007."""
    for cell in CELLS:
        row = _cell(comparison, cell)
        traj = float(row["trajectory_mae"].iloc[0])
        extrap = float(row["extrapolation_mae"].iloc[0])
        if cell == "B0006":
            assert extrap < traj, (
                f"B0006: extrapolation {extrap} no longer beats "
                f"trajectory {traj}"
            )
        else:
            assert traj < extrap, (
                f"{cell}: trajectory {traj} no longer beats "
                f"extrapolation {extrap}"
            )


def test_extrapolation_follows_the_measured_fade():
    """Why B0006 is the one cell the extrapolation wins: it fades
    almost linearly, so the early line lands nearest its end of life."""
    if not CLEAN.exists():
        pytest.skip("clean dataset not built")
    df = pd.read_csv(CLEAN)
    mae = (
        RT.extrapolation_loco(df)
        .set_index("test_cell")["extrapolation_mae"]
    )
    assert mae.loc["B0006"] == mae.loc[list(CELLS)].min()
    assert mae.loc["B0005"] > mae.loc["B0006"]
    assert mae.loc["B0007"] > mae.loc["B0006"]


def test_calibrated_band_uses_three_cells(band):
    assert (band["n_calibration_cells"] == 3).all()


def test_calibrated_band_does_not_reach_nominal_coverage(band):
    """The report says a calibrated 10th/90th band is not possible
    here, because the coverage swings from 0% to 100%."""
    mean_cov = float(band["coverage_pct"].mean())
    assert mean_cov < 80.0, (
        f"mean coverage {mean_cov} is close to nominal; the report claim "
        "that a calibrated band is not possible needs revisiting"
    )
    b0006 = float(_cell(band, "B0006")["coverage_pct"].iloc[0])
    assert b0006 == 0.0, "B0006 should still be wholly outside the band"
    assert float(band["coverage_pct"].max()) >= 80.0
    assert float(band["coverage_pct"].min()) <= 20.0
