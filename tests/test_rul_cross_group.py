"""
test_rul_cross_group.py
-----------------------
The random-load RUL tables must match the labels and the story the
report tells.

These tests read the artifacts rul_cross_group.py writes and check:

  * the labels use the production rule (they equal the random-load
    check's eol_discharge, which is computed independently);
  * each protocol scores both methods and the baseline on every
    held-out cell, against the right number of references;
  * the numbers reproduce from a fresh evaluation;
  * protocol (a) is the group where neither method beats the
    fleet-lifetime baseline, and (b) is where both do;
  * the fade-rate extrapolation is the same in every protocol
    (it reads only the held-out cell's own opening discharges).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import rul_cross_group as RG  # noqa: E402

PROC = REPO / "data" / "processed"

LABELS = PROC / "rul_random_load_labels.csv"
RANDOM_CHECK = PROC / "random_load_rul_check.csv"
G6_LOCO = PROC / "rul_cross_group_g6_loco.csv"
G1_TO_G6 = PROC / "rul_cross_group_g1_to_g6.csv"
G6_TO_G1 = PROC / "rul_cross_group_g6_to_g1.csv"
SWEEP = PROC / "rul_extrapolation_window_sweep.csv"
METHOD_COMPARISON = PROC / "rul_method_comparison.csv"

G1 = ("B0005", "B0006", "B0007", "B0018")
G6 = ("B0042", "B0043", "B0044")

REF_COUNTS = {
    "g6_loco": 2,
    "g1_to_g6": 4,
    "g6_to_g1": 3,
}


def _load(path):
    if not path.exists():
        pytest.skip(f"rul_cross_group.py has not produced {path.name}")
    return pd.read_csv(path)


@pytest.fixture(scope="module")
def labels():
    return _load(LABELS)


@pytest.fixture(scope="module")
def protocols():
    return {
        "g6_loco": _load(G6_LOCO),
        "g1_to_g6": _load(G1_TO_G6),
        "g6_to_g1": _load(G6_TO_G1),
    }


@pytest.fixture(scope="module")
def sweep():
    return _load(SWEEP)


def test_labels_reach_the_four_usable_random_load_cells(labels):
    assert set(labels["cell_id"]) == set(G6) | {"B0039"}
    assert labels["reaches_80"].all()


def test_labels_match_the_independent_random_load_check(labels):
    """The label rule is the production one, so its eol_discharge must
    equal the number random_load.py computes for the same cells."""

    check = _load(RANDOM_CHECK)

    for cell in G6 + ("B0039",):

        want = float(
            check.loc[check["cell_id"] == cell, "eol_discharge"].iloc[0]
        )
        got = float(
            labels.loc[labels["cell_id"] == cell, "eol_discharge"].iloc[0]
        )

        assert got == want, f"{cell}: label {got} != check {want}"


@pytest.mark.parametrize(
    "key,cells",
    [("g6_loco", G6), ("g1_to_g6", G6), ("g6_to_g1", G1)],
)
def test_protocol_scores_both_methods_and_the_baseline(protocols, key, cells):
    frame = protocols[key]

    assert set(frame["test_cell"]) == set(cells)
    assert {
        "trajectory_mae",
        "extrapolation_mae",
        "fleet_lifetime_baseline_mae",
        "n_points",
        "n_reference_cells",
    } <= set(frame.columns)
    assert (frame["n_points"] > 0).all()
    assert (frame["n_reference_cells"] == REF_COUNTS[key]).all()

    for column in (
        "trajectory_mae",
        "extrapolation_mae",
        "fleet_lifetime_baseline_mae",
    ):
        assert np.isfinite(frame[column]).all()


def test_protocol_a_neither_method_beats_the_baseline():
    """This is the one-line caveat the report makes."""

    frame = _load(G6_LOCO)

    for _, row in frame.iterrows():

        assert row["trajectory_mae"] >= row["fleet_lifetime_baseline_mae"]
        assert row["extrapolation_mae"] >= row["fleet_lifetime_baseline_mae"]


def test_protocol_b_both_methods_beat_the_baseline():
    frame = _load(G1_TO_G6)

    assert (
        frame["trajectory_mae"] < frame["fleet_lifetime_baseline_mae"]
    ).all()
    assert (
        frame["extrapolation_mae"] < frame["fleet_lifetime_baseline_mae"]
    ).all()


def test_protocol_c_trajectory_beats_the_mean_not_the_extrapolation():
    frame = _load(G6_TO_G1)

    base = float(frame["fleet_lifetime_baseline_mae"].mean())
    traj = float(frame["trajectory_mae"].mean())
    extrap = float(frame["extrapolation_mae"].mean())

    assert traj < base, "trajectory no longer beats the mean baseline"
    assert extrap > base, "extrapolation now beats the mean baseline"
    assert (
        frame["trajectory_mae"] < frame["fleet_lifetime_baseline_mae"]
    ).sum() == 3


def test_extrapolation_is_the_same_in_every_protocol():
    """It reads only the held-out cell's own opening discharges, so the
    G6 cells scored in (a) and (b) must carry the same number."""

    a = _load(G6_LOCO).set_index("test_cell")["extrapolation_mae"]
    b = _load(G1_TO_G6).set_index("test_cell")["extrapolation_mae"]

    for cell in G6:
        assert a.loc[cell] == pytest.approx(b.loc[cell], abs=1e-9)


def _dataset():
    raw = RG.resolve_raw_dir()
    if not Path(raw).exists():
        pytest.skip("raw NASA files not present")
    return RG.rul_dataset(RG.CLEAN, raw)


def _rerun():
    dataset = _dataset()
    return {
        "g6_loco": RG.cross_group_evaluate(
            dataset, RG.G6_CELLS, RG.G6_CELLS,
            "leave-one-cell-out: G6", loco=True,
        ),
        "g1_to_g6": RG.cross_group_evaluate(
            dataset, RG.G1_CELLS, RG.G6_CELLS, "train G1, test G6",
        ),
        "g6_to_g1": RG.cross_group_evaluate(
            dataset, RG.G6_CELLS, RG.G1_CELLS, "train G6, test G1",
        ),
    }


def test_numbers_reproduce(protocols):
    fresh = _rerun()

    for key, frame in protocols.items():

        got = fresh[key].set_index("test_cell")
        want = frame.set_index("test_cell")

        assert set(got.index) == set(want.index)

        for column in (
            "trajectory_mae",
            "extrapolation_mae",
            "fleet_lifetime_baseline_mae",
        ):
            assert got[column].round(6).to_dict() == (
                want[column].round(6).to_dict()
            ), f"{key}.{column} does not reproduce"


def test_window_sweep_covers_every_labelled_cell(sweep):
    assert set(sweep["window"]) == set(RG.SWEEP_WINDOWS)
    assert set(sweep["cell_id"]) == set(RG.SWEEP_CELLS)
    assert len(sweep) == len(RG.SWEEP_CELLS) * len(RG.SWEEP_WINDOWS)
    over = sweep[sweep["window"] > sweep["discharges"]]
    assert set(over["cell_id"]) == {"B0039"}
    assert np.isfinite(sweep["mae"]).all()


def test_window_sweep_default_column_matches_the_scored_method(sweep):
    """The 20-discharge column is the extrapolation the report already
    scores, so it must equal those committed numbers exactly."""

    w20 = (
        sweep[sweep["window"] == RG.EXTRAP_WINDOW]
        .set_index("cell_id")["mae"]
    )
    g1 = _load(METHOD_COMPARISON).set_index("test_cell")["extrapolation_mae"]

    for cell in G6:
        wanted = float(
            _load(G1_TO_G6)
            .set_index("test_cell")["extrapolation_mae"]
            .loc[cell]
        )
        assert w20.loc[cell] == pytest.approx(wanted, abs=1e-6)

    for cell in G1:
        assert w20.loc[cell] == pytest.approx(float(g1.loc[cell]), abs=1e-6)


def test_longer_window_does_not_help_on_held_out_cells(sweep):
    """This is the report's decision: the mean falls, but only because
    one flat-early cell stops being an outlier; the median does not
    fall and more than half the cells get worse."""

    lo = sweep[sweep["window"] == RG.SWEEP_WINDOWS[0]].set_index("cell_id")
    hi = sweep[sweep["window"] == RG.SWEEP_WINDOWS[-1]].set_index("cell_id")

    assert lo["mae"].mean() > hi["mae"].mean()
    assert hi["mae"].median() >= lo["mae"].median()

    worse = int((hi["mae"] > lo["mae"]).sum())
    assert worse > len(RG.SWEEP_CELLS) / 2


def test_window_sweep_reproduces(sweep):
    fresh = RG.window_sweep(_dataset())

    got = fresh.set_index(["cell_id", "window"])["mae"].round(6)
    want = sweep.set_index(["cell_id", "window"])["mae"].round(6)

    assert got.to_dict() == want.to_dict()
