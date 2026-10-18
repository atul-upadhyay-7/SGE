"""
test_window_partial.py
----------------------
The report calls this a "partial discharge" and the 4.0-3.4 V window
is easy to read as a small early slice. This test pins the measured
answer instead of the phrase.

``data/processed/soh_window_partial.csv`` is the artifact
(src/window_partial.py): one row per kept cell giving the mean share
of a discharge's capacity (vw_capacity_ah / capacity_ah) and of its
duration (vw_time_s / discharge_duration_s) that lies inside the
4.0-3.4 V window, plus a MEAN row. These tests check:

  * the per-cell shares recompute from the clean dataset;
  * every share is a genuine fraction in (0, 1];
  * the MEAN row is the mean of the per-cell rows;
  * the cold G9 cell holds the least, so the window is not uniformly
    "most of the discharge";
  * the README limits row and the model card both carry the numbers.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent

PROC = REPO / "data" / "processed"
CLEAN = PROC / "nasa_ml_dataset_clean.csv"
CSV = PROC / "soh_window_partial.csv"
README = REPO / "README.md"
MODEL_CARD = REPO / "docs" / "MODEL_CARD.md"

TOL = 1e-6


@pytest.fixture(scope="module")
def frame():
    if not CSV.exists():
        pytest.skip("window_partial.py has not produced its CSV")
    return pd.read_csv(CSV)


@pytest.fixture(scope="module")
def recomputed():
    clean = pd.read_csv(CLEAN)
    values = clean.copy()
    values["capacity_inside_window_pct"] = (
        values["vw_capacity_ah"] / values["capacity_ah"] * 100.0
    )
    values["time_inside_window_pct"] = (
        values["vw_time_s"] / values["discharge_duration_s"] * 100.0
    )
    return (
        values.groupby("cell_id")[
            ["capacity_inside_window_pct", "time_inside_window_pct"]
        ]
        .mean()
    )


def test_per_cell_shares_recompute(frame, recomputed):
    cells = frame[frame["cell_id"] != "MEAN"].set_index("cell_id")

    assert set(cells.index) == set(recomputed.index)

    for cell in recomputed.index:
        for column in (
            "capacity_inside_window_pct",
            "time_inside_window_pct",
        ):
            assert np.isclose(
                cells.loc[cell, column],
                recomputed.loc[cell, column],
                rtol=0.0,
                atol=TOL,
            ), f"{cell}: {column} disagrees with the clean dataset"


def test_every_share_is_a_fraction(frame):
    cells = frame[frame["cell_id"] != "MEAN"]

    for column in (
        "capacity_inside_window_pct",
        "time_inside_window_pct",
    ):
        assert (cells[column] > 0.0).all()
        assert (cells[column] <= 100.0).all()


def test_mean_row_is_the_mean_of_the_cells(frame):
    cells = frame[frame["cell_id"] != "MEAN"]
    mean = frame[frame["cell_id"] == "MEAN"].iloc[0]

    assert np.isclose(
        mean["capacity_inside_window_pct"],
        cells["capacity_inside_window_pct"].mean(),
        atol=TOL,
    )
    assert np.isclose(
        mean["time_inside_window_pct"],
        cells["time_inside_window_pct"].mean(),
        atol=TOL,
    )


def test_the_window_is_not_uniformly_most_of_the_discharge(frame):
    """The cold G9 cell traverses the band early and holds the least, so
    "partial" is not a uniform "most of the discharge"."""

    cells = frame[frame["cell_id"] != "MEAN"].set_index("cell_id")

    assert cells["capacity_inside_window_pct"].idxmin() == "B0053"
    assert cells["time_inside_window_pct"].idxmin() == "B0053"


def test_readme_limits_row_carries_the_mean(frame):
    text = README.read_text()
    mean = frame[frame["cell_id"] == "MEAN"].iloc[0]

    assert "does not mean small" in text
    assert f"{mean['capacity_inside_window_pct']:.0f}%" in text
    assert f"{mean['time_inside_window_pct']:.0f}%" in text


def test_model_card_carries_the_per_cell_table(frame):
    text = MODEL_CARD.read_text()
    cells = frame[frame["cell_id"] != "MEAN"]

    assert "How partial the window is" in text
    for _, row in cells.iterrows():
        assert f"| {row['cell_id']} | {row['capacity_inside_window_pct']:.2f} |" in text
