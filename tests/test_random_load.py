"""
test_random_load.py
-------------------
The report claims that the random-load groups are excluded for
comparability, not because they fail to age: the checked cells reach
80% and pass the same keep filter.

These tests pin that claim against the CSV the pipeline writes and,
when the raw dataset is present, against the audit function itself,
so the count cannot be typed into the report and the "only four G1
cells reach 80%" statement stays corrected. They also hold the
reference-capacity guard: a trusted cell's reference must not be
under half the median of its later valid discharges, and B0041 — the
excluded cell whose opening runs are a measurement failure — is the
one that trips it.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

CHECK = REPO / "data" / "processed" / "random_load_rul_check.csv"
AUDIT = REPO / "data" / "processed" / "cell_audit.csv"

RANDOM_CELLS = {"B0038", "B0039", "B0040", "B0041", "B0042", "B0043", "B0044"}
REACH_AND_KEPT = {"B0039", "B0042", "B0043", "B0044"}


@pytest.fixture(scope="module")
def check():
    if not CHECK.exists():
        pytest.skip("random_load.py has not produced random_load_rul_check.csv")
    return pd.read_csv(CHECK)


def test_check_covers_both_random_load_groups(check):
    assert set(check["cell_id"]) == RANDOM_CELLS
    assert set(check["group"]) == {"G5", "G6"}
    assert (check["threshold"] == 80.0).all()


def test_four_cells_reach_and_pass_but_stay_out_of_the_model(check):
    reaches = int(check["reaches_80"].sum())
    usable = int((check["reaches_80"] & check["keep"]).sum())
    assert reaches == 5, "the report says 5 of 7 reach 80%"
    assert usable == 4, "the report says 4 of 7 reach 80% and pass keep"
    reached = set(check.loc[check["reaches_80"] & check["keep"], "cell_id"])
    assert reached == REACH_AND_KEPT


def test_random_load_cells_are_not_in_the_kept_model_set(check):
    if not AUDIT.exists():
        pytest.skip("cell_audit.csv not built")
    kept = set(pd.read_csv(AUDIT).loc[lambda d: d["keep"], "cell_id"])
    assert not (REACH_AND_KEPT & kept), (
        "random-load cells reached the trained set; the report says they are "
        "left out for changing load and temperature"
    )


def test_b0041_reaches_but_is_excluded_by_the_reference_rule(check):
    row = check[check["cell_id"] == "B0041"].iloc[0]
    assert bool(row["reaches_80"]) is True
    assert bool(row["keep"]) is False
    assert row["reason"], "an excluded cell must carry its reason"


def test_b0041_reference_is_a_measured_artifact(check):
    """B0041's opening discharges are a measurement failure: the check
    records that its reference is under half the median of its later
    valid discharges, and that the opening runs are almost no load
    (a small current, almost no voltage drop) rather than a low-
    capacity cell."""

    row = check[check["cell_id"] == "B0041"].iloc[0]

    assert bool(row["reference_is_artifact"]) is True
    assert float(row["reference_capacity_ah"]) < 0.5 * float(
        row["later_median_capacity_ah"]
    )
    assert float(row["early_current_mean_abs_a"]) < 0.5
    assert float(row["early_voltage_drop_v"]) < 0.5


def test_kept_random_load_references_are_not_artifacts(check):
    """The guard that fails if a trusted cell's reference is under half
    the median of its later valid discharges: no kept random-load cell
    may trip it."""

    kept = check[check["keep"].astype(bool)]
    assert not kept.empty

    for _, row in kept.iterrows():

        assert bool(row["reference_is_artifact"]) is False
        assert float(row["reference_capacity_ah"]) >= 0.5 * float(
            row["later_median_capacity_ah"]
        ), f"{row['cell_id']}: reference is a measurement artifact"


def test_audit_function_matches_the_artifact(check):
    import random_load as RL

    try:
        fresh = RL.audit_random_load()
    except FileNotFoundError:
        pytest.skip("raw .mat dataset not present")

    left = check.set_index("cell_id")[["reaches_80", "keep"]].sort_index()
    right = fresh.set_index("cell_id")[["reaches_80", "keep"]].sort_index()
    assert left.equals(right)
