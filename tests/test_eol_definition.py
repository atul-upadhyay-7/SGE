"""
test_eol_definition.py
----------------------
End of life must be one number per cell, the same in every table.

The definition lives in preprocessing.first_eol_discharge: the 1-based
discharge number of the first discharge whose measured SOH is strictly
below 80%. Each stage writes its own CSV, so the number is recomputed
in several places and can drift. It did: rul_trajectory.py reported a
0-based position, which made B0005 read 106 there and 107 in the
cross-group tables.

These tests read every committed table that reports an end-of-life
discharge and check three things:

  * the tables agree with each other, cell by cell;
  * they agree with the canonical definition recomputed from the
    cleaned SOH;
  * the helper itself is 1-based and uses a strict below-threshold
    test.

Cells that appear in only one table (for example B0041, an excluded
cell that random_load_rul_check.csv alone keeps) cannot disagree with
anything, so they are compared only when a second table reports them.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from preprocessing import EOL_SOH_THRESHOLD, first_eol_discharge  # noqa: E402

PROC = REPO / "data" / "processed"
CLEAN = PROC / "nasa_ml_dataset_clean.csv"

# Every committed table that reports an end-of-life discharge.
# (file, cell column, end-of-life column)
EOL_TABLES = [
    ("rul_trajectory_results.csv", "test_cell", "eol_discharge"),
    ("rul_method_comparison.csv", "test_cell", "eol_discharge"),
    ("rul_cross_group_g6_loco.csv", "test_cell", "eol_discharge"),
    ("rul_cross_group_g1_to_g6.csv", "test_cell", "eol_discharge"),
    ("rul_cross_group_g6_to_g1.csv", "test_cell", "eol_discharge"),
    ("rul_random_load_labels.csv", "cell_id", "eol_discharge"),
    ("random_load_rul_check.csv", "cell_id", "eol_discharge"),
    ("cell_audit.csv", "cell_id", "eol_discharge"),
    ("rul_extrapolation_window_sweep.csv", "cell_id", "eol_discharge"),
    ("rul_degradation_rates.csv", "cell_id", "eol_cycle"),
]


def reported_by_table():
    """
    {table_name: {cell_id: eol_number}} for every table that reports one.

    Missing files are skipped so this test can run on a partial tree.
    Rows with NaN (a cell that never retires, or an excluded cell) are
    dropped: they carry no number to agree on.
    """

    by_table = {}

    for name, key, eol in EOL_TABLES:

        path = PROC / name

        if not path.exists():
            continue

        frame = pd.read_csv(path)

        assert key in frame.columns, f"{name} lost its {key!r} column"
        assert eol in frame.columns, f"{name} lost its {eol!r} column"

        values = frame[[key, eol]].dropna()

        by_table[name] = {
            str(row[key]): int(round(float(row[eol])))
            for _, row in values.iterrows()
        }

    return by_table


def reported_by_cell():
    """{cell_id: {table_name: eol_number}}, dropping unnamed cells."""

    by_cell = {}

    for name, per_cell in reported_by_table().items():
        for cell, value in per_cell.items():
            by_cell.setdefault(cell, {})[name] = value

    return by_cell


def canonical_from_clean():
    """
    {cell_id: eol_number} recomputed from the cleaned SOH with the one
    definition in preprocessing. Only the kept cells live in this file.
    """

    frame = pd.read_csv(
        CLEAN, usecols=["cell_id", "discharge_index", "soh"]
    )

    canonical = {}

    for cell, group in frame.groupby("cell_id"):

        value = first_eol_discharge(
            group["soh"], group["discharge_index"]
        )

        if value is not None:
            canonical[str(cell)] = value

    return canonical


def test_no_two_tables_disagree_for_the_same_cell():
    """The failure this file exists for: B0005 was 106 and 107 at once."""

    by_cell = reported_by_cell()

    compared = {
        cell: tables
        for cell, tables in by_cell.items()
        if len(tables) >= 2
    }

    assert compared, "no cell is reported by two tables; test is vacuous"

    disagreements = {
        cell: tables
        for cell, tables in compared.items()
        if len(set(tables.values())) > 1
    }

    assert not disagreements, (
        "tables disagree on the end-of-life discharge for: "
        + ", ".join(
            f"{cell} {tables}" for cell, tables in sorted(disagreements.items())
        )
    )


def test_tables_agree_with_the_canonical_definition():
    """The reported number is first_eol_discharge over the cleaned SOH."""

    by_cell = reported_by_cell()
    canonical = canonical_from_clean()

    assert canonical, f"{CLEAN.name} holds no cell that retires"

    checked = 0

    for cell, value in canonical.items():

        for table, reported in by_cell.get(cell, {}).items():

            assert reported == value, (
                f"{table} reports {cell} end of life at {reported}, "
                f"but the cleaned SOH crosses {EOL_SOH_THRESHOLD:g}% at "
                f"discharge {value}"
            )

            checked += 1

    assert checked, "no reported cell could be checked against the clean data"


def test_no_table_invents_an_end_of_life_the_clean_data_does_not_show():
    """A cell that never retires must not be given a number."""

    by_cell = reported_by_cell()
    canonical = canonical_from_clean()

    clean_cells = set(pd.read_csv(CLEAN, usecols=["cell_id"])["cell_id"].astype(str))

    for cell in clean_cells - set(canonical):

        for table, reported in by_cell.get(cell, {}).items():

            pytest.fail(
                f"{table} reports {cell} end of life at {reported}, but "
                f"{cell} never falls below {EOL_SOH_THRESHOLD:g}% in the "
                "cleaned data"
            )


def test_the_helper_is_one_based_and_strictly_below():
    """The definition itself: first discharge below threshold, from 1."""

    soh = np.array([100.0, 90.0, 80.0, 79.9, 70.0])

    # 80.0 exactly is not below 80, so the first crossing is position 4.
    assert first_eol_discharge(soh) == 4

    # The first discharge cannot be 0.
    assert first_eol_discharge(np.array([79.0, 60.0])) == 1

    # A cell that never retires has no number.
    assert first_eol_discharge(np.array([100.0, 90.0, 80.0])) is None

    # A custom threshold and an explicit numbering shift the answer.
    # 90.0 is not below 90 either, so a 90% threshold first trips at 80.0.
    assert first_eol_discharge(soh, threshold=90.0) == 3
    assert (
        first_eol_discharge(
            soh, discharge_index=np.array([10, 11, 12, 13, 14])
        )
        == 13
    )


def test_the_regression_pair_is_still_covered():
    """
    Keep the test honest: the two tables that once disagreed for B0005
    must both still report B0005, or this file stops guarding the bug.
    """

    by_cell = reported_by_cell()

    for cell in ("B0005", "B0006", "B0007", "B0018"):

        tables = by_cell.get(cell, {})

        assert "rul_trajectory_results.csv" in tables, cell
        assert "rul_cross_group_g6_to_g1.csv" in tables, cell
