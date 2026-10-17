"""
test_rul_range.py
-----------------
RUL ships as one indicative range, not a point estimate.

The single method decision the report makes: which method is best
depends on the cell and the reference group, so the shipped RUL is the
min-to-max of the trajectory matcher, the early-fade extrapolation and
the fleet-lifetime average on every discharge. These tests read the
artifact src/rul_trajectory.py writes (rul_prediction_bands.csv) and
check:

  * the three methods and the low/high envelope are present and
    ordered (low == min, high == max, every method inside);
  * the trajectory point is the one the matcher returns and the
    extrapolation is the same straight line the other tables score;
  * the numbers reproduce from a fresh evaluation;
  * the two claims the paragraph makes hold: the fleet-lifetime
    average beats both methods on every G6 cell, and the matcher beats
    the average on three of four G1 cells across groups;
  * the README carries the generated decision paragraph.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import rul_trajectory as RT  # noqa: E402

PROC = REPO / "data" / "processed"
README = REPO / "README.md"

BANDS = PROC / "rul_prediction_bands.csv"
CLEAN = PROC / "nasa_ml_dataset_clean.csv"
EXTRAP_BANDS = PROC / "rul_extrapolation_bands.csv"
G6_LOCO = PROC / "rul_cross_group_g6_loco.csv"
G6_TO_G1 = PROC / "rul_cross_group_g6_to_g1.csv"

G1 = ("B0005", "B0006", "B0007", "B0018")

METHODS = ("predicted_rul", "lifetime_average_rul", "extrapolated_rul")


def _load(path):
    if not path.exists():
        pytest.skip(f"pipeline has not produced {path.name}")
    return pd.read_csv(path)


@pytest.fixture(scope="module")
def bands():
    return _load(BANDS)


def test_bands_carry_the_three_methods_and_the_envelope(bands):
    assert set(bands["cell_id"]) == set(G1)
    assert {
        "predicted_rul",
        "lifetime_average_rul",
        "extrapolated_rul",
        "rul_low",
        "rul_high",
    } <= set(bands.columns)
    assert bands[list(METHODS) + ["rul_low", "rul_high"]].notna().all().all()


def test_low_and_high_are_the_method_envelope(bands):
    envelope_lo = bands[list(METHODS)].min(axis=1)
    envelope_hi = bands[list(METHODS)].max(axis=1)

    assert np.allclose(bands["rul_low"], envelope_lo)
    assert np.allclose(bands["rul_high"], envelope_hi)

    for method in METHODS:
        assert (bands["rul_low"] <= bands[method] + 1e-9).all()
        assert (bands[method] <= bands["rul_high"] + 1e-9).all()


def test_extrapolation_matches_the_scored_straight_line(bands):
    """The extrapolation in the range is the same line the other RUL
    tables score, so it must equal rul_extrapolation_bands on the
    discharges both cover."""

    if not EXTRAP_BANDS.exists():
        pytest.skip("rul_extrapolation_bands.csv not built")

    other = _load(EXTRAP_BANDS).rename(
        columns={"extrapolated_rul": "extrapolated_other"}
    )

    merged = bands.merge(other, on=["cell_id", "cycle"], how="inner")

    assert not merged.empty
    assert np.allclose(
        merged["extrapolated_rul"],
        merged["extrapolated_other"],
        atol=1e-6,
    )


def _fresh():
    df = _load(CLEAN)
    preds = {
        cell: pd.read_csv(
            PROC / f"soh_predictions_{cell}.csv"
        ).sort_values("cycle")
        for cell in G1
    }
    return RT.prediction_bands(df, preds, cells=list(G1))


def test_range_reproduces_from_a_fresh_evaluation(bands):
    fresh = _fresh()

    got = fresh.set_index(["cell_id", "cycle"])
    want = bands.set_index(["cell_id", "cycle"])

    assert set(got.index) == set(want.index)

    for column in METHODS + ("rul_low", "rul_high"):
        assert np.allclose(
            got[column], want[column], atol=1e-6
        ), f"{column} does not reproduce"


def test_fleet_lifetime_average_beats_both_methods_inside_g6():
    """The report's first claim. Leave-one-cell-out inside G6, the
    baseline is at or below both methods on every held-out cell."""

    frame = _load(G6_LOCO)

    for _, row in frame.iterrows():

        assert row["fleet_lifetime_baseline_mae"] <= row["trajectory_mae"]
        assert (
            row["fleet_lifetime_baseline_mae"]
            <= row["extrapolation_mae"]
        )


def test_matcher_beats_the_average_on_three_of_four_g1_cells():
    """The report's second claim, across groups (train G6, test G1)."""

    frame = _load(G6_TO_G1)

    assert set(frame["test_cell"]) == set(G1)

    beat = (
        frame["trajectory_mae"] < frame["fleet_lifetime_baseline_mae"]
    )

    assert int(beat.sum()) == 3
    assert frame.loc[~beat, "test_cell"].tolist() == ["B0006"]


def test_readme_states_the_range_decision():
    text = README.read_text()

    assert "One decision: RUL ships as a range" in text
    assert "indicative range" in text
