"""
test_random_load_transfer.py
----------------------------
The report claims the partial-window SOH model transfers to the
random-load cells it was never trained on, so their exclusion is a
comparability choice and not a transfer failure.

``data/processed/soh_random_load_transfer.csv`` is the artifact: the
shipped ``soh_window_all`` model scored on B0039, B0042, B0043 and
B0044 with the online window features, next to the window formula on
exactly the same discharges. These tests pin that claim four ways:

  * the shipped model's ``trained_on`` list excludes every cell scored
    here, so the number cannot quietly become an in-sample check;
  * each reported score reproduces from a fresh run;
  * the model beats the window formula on every cell, which is what
    makes the exclusion "comparability, not failure";
  * the docs/RESULTS.md caveat — B0039 is the one cell whose R² is
    below a constant mean — stays true.
"""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

PROC = REPO / "data" / "processed"
CSV = PROC / "soh_random_load_transfer.csv"
AUDIT = PROC / "cell_audit.csv"
CHECK = PROC / "random_load_rul_check.csv"
MODEL_JSON = REPO / "models" / "soh_window_all.json"
RESULTS = REPO / "docs" / "RESULTS.md"

CELLS = {"B0039", "B0042", "B0043", "B0044"}
TOL = 0.01


@pytest.fixture(scope="module")
def frame():
    if not CSV.exists():
        pytest.skip("random_load_transfer.py has not produced its CSV")
    return pd.read_csv(CSV)


@pytest.fixture(scope="module")
def cells(frame):
    return frame[frame["cell_id"] != "MEAN"]


def test_artifact_covers_exactly_the_kept_random_load_cells(frame, cells):
    """The four cells the audit keeps, plus a mean row, and nothing else."""

    assert set(cells["cell_id"]) == CELLS
    assert set(frame["cell_id"]) - CELLS == {"MEAN"}
    assert set(cells["group"]) == {"G5", "G6"}

    if CHECK.exists():
        discharges = (
            pd.read_csv(CHECK).set_index("cell_id")["discharges"]
        )
        for _, row in cells.iterrows():
            assert int(row["n_cycles"]) == int(
                discharges.loc[row["cell_id"]]
            )


def test_model_never_trained_on_these_cells():
    """The scored model is the shipped one, and it excludes every cell
    scored here — otherwise the transfer claim is in-sample."""

    meta = json.loads(MODEL_JSON.read_text())

    assert not (CELLS & set(meta["trained_on"]))
    assert len(meta["trained_on"]) == 9

    if AUDIT.exists():
        kept = set(
            pd.read_csv(AUDIT).loc[lambda d: d["keep"], "cell_id"]
        )
        assert kept == set(meta["trained_on"]), (
            "the shipped model was not trained on exactly the kept cells"
        )


def test_reported_scores_reproduce_from_a_fresh_run(frame, cells):
    """Each cell's MAE and R² must match a fresh scoring run."""

    import random_load_transfer as RLT

    try:
        fresh = RLT.transfer_table()
    except FileNotFoundError:
        pytest.skip("raw .mat dataset not present")

    left = cells.set_index("cell_id").sort_index()
    right = (
        fresh[fresh["cell_id"] != "MEAN"]
        .set_index("cell_id")
        .sort_index()
    )

    assert list(left.index) == list(right.index)

    for column in (
        "model_mae_pct",
        "model_r2",
        "window_formula_mae_pct",
        "window_formula_r2",
    ):
        assert (left[column] - right[column]).abs().max() <= TOL, column


def test_the_model_beats_the_window_formula_on_every_cell(cells):
    """This is what makes the exclusion a comparability choice: the
    model is not failing, it is winning, on the cells it never saw."""

    assert (
        cells["model_mae_pct"] < cells["window_formula_mae_pct"]
    ).all()
    assert cells["model_mae_pct"].mean() < (
        cells["window_formula_mae_pct"].mean()
    )


def test_b0039_is_the_one_cell_without_fade_tracking(cells):
    """The docs/RESULTS.md caveat: three of the four track the fade (R²
    above zero) and B0039 is the exception."""

    flat = cells[cells["model_r2"] <= 0.0]

    assert set(flat["cell_id"]) == {"B0039"}
    assert int((cells["model_r2"] > 0.0).sum()) == 3


def test_readme_reports_the_transfer_result(frame):
    """The generated region must carry the section and the mean model
    error, so the table cannot be dropped from the report silently."""

    text = RESULTS.read_text()
    mean = frame[frame["cell_id"] == "MEAN"].iloc[0]

    assert "Random-load transfer" in text
    assert f"{float(mean['model_mae_pct']):.2f}" in text
