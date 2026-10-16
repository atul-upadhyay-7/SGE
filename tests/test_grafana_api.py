"""
Tests for the Grafana JSON datasource adapter.

The table branch used to declare every column as "string",
which made Grafana left-align MAE and RMSE as text with no
numeric formatting. cursor.description was the original source
of those types and reports a null type on this interpreter, so
the declared types now come from PRAGMA table_info. These tests
pin both halves: the mapping itself, and the fact that the
adapter really does emit "number" for a numeric column read
out of the shipped database.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(
    0, str(ROOT / "dashboard")
)

grafana_api = pytest.importorskip("grafana_api")


DATABASE = ROOT / "data" / "battery_pdm.db"


@pytest.mark.parametrize(
    "declared,expected",
    [
        ("TEXT", "string"),
        ("VARCHAR(20)", "string"),
        ("REAL", "number"),
        ("INTEGER", "number"),
        ("DOUBLE", "number"),
        ("NUMERIC", "number"),
        ("BIGINT", "number"),
        # SQLite is loosely typed and may attach a width or a
        # constraint, so the mapping must tolerate suffixes.
        ("REAL(10,4)", "number"),
        ("DOUBLE PRECISION NOT NULL", "number"),
        # Undeclared or expression columns have no type to read.
        ("", "string"),
        (None, "string"),
    ],
)
def test_sqlite_type_maps_to_frame_type(declared, expected):

    assert (
        grafana_api.sqlite_type_to_frame_type(declared)
        == expected
    )


def test_pragmatic_row_provides_declared_types():
    """
    The reason PRAGMA is used instead of cursor.description:
    on this interpreter description reports a null type for
    every column, so relying on it silently produced an
    all-string table.
    """

    if not DATABASE.exists():

        pytest.skip("dashboard database not built yet")

    conn = sqlite3.connect(DATABASE)

    try:

        described = [
            row[1]
            for row in conn.execute(
                "SELECT * FROM [soh_results]"
            ).description
        ]

        pragma = [
            row[2]
            for row in conn.execute(
                "PRAGMA table_info([soh_results])"
            ).fetchall()
        ]

    finally:

        conn.close()

    assert set(described) <= {None}

    assert "REAL" in pragma


def test_discharges_left_is_database_backed_not_a_constant():
    """
    The stat tile used to hardcode 16.0 with a fabricated +-20%
    range. It must now read rul_prediction_bands: the point value is
    the mean of each cell's latest prediction and the range is the
    mean of each cell's own reference spread, so p10 < value < p90
    and neither equals the old constants.
    """

    if not DATABASE.exists():

        pytest.skip("dashboard database not built yet")

    frame = grafana_api.handle_table("table:stat_discharges_left")

    assert frame["rows"], "expected a data row"

    value, rangetext = frame["rows"][0]

    assert isinstance(value, (int, float))

    lo, _, hi = rangetext.partition("–")
    lo, hi = float(lo.strip()), float(hi.strip().split()[0])

    assert lo <= value <= hi
    assert (value, lo, hi) != (16.0, 12.8, 19.2)


def test_rul_prediction_bands_are_ordered_and_from_database():
    """
    The percentiles must be stored, not ratios of the point estimate
    (predicted*0.8 / *1.2 was wrong because the true reference spread
    is not a fixed percentage). p10 <= predicted <= p90 every row.
    """

    if not DATABASE.exists():

        pytest.skip("dashboard database not built yet")

    frame = grafana_api.handle_table("table:rul_range")

    names = [c["text"] for c in frame["columns"]]

    assert names[3:6] == [
        "Predicted Discharges", "Reference Spread Low", "Reference Spread High",
    ]

    predicted = names.index("Predicted Discharges")
    p10 = names.index("Reference Spread Low")
    p90 = names.index("Reference Spread High")

    assert frame["rows"]

    for row in frame["rows"]:

        assert row[p10] <= row[predicted] <= row[p90]


def test_cell_variable_chains_to_group_in_search():
    """
    The cell variable query is "cells:$group". Grafana substitutes
    the group value, so the API sees "cells:G1" and must return only
    G1 cells; "cells:All" (group on All) must fall back to every
    audited cell rather than filtering on the literal "All".
    """

    if not DATABASE.exists():

        pytest.skip("dashboard database not built yet")

    client = grafana_api.app.test_client()

    all_cells = client.post(
        "/search", json={"target": "cells:All"}
    ).get_json()

    g1 = client.post(
        "/search", json={"target": "cells:G1"}
    ).get_json()

    assert all_cells
    assert set(g1) < set(all_cells)
    assert "B0005" in g1
    assert "B0006" in g1
    assert "B0007" in g1
    assert "B0018" in g1


def test_cell_dropdown_filters_table_panels():
    """
    A per-cell table must obey the cell variable, not silently show
    every cell while the dropdown is on one.
    """

    if not DATABASE.exists():

        pytest.skip("dashboard database not built yet")

    frame = grafana_api.handle_table(
        "table:rul_range", {"cell": {"value": "B0006"}}
    )

    assert frame["rows"]

    for row in frame["rows"]:

        assert row[0] == "B0006"


def test_query_returns_numeric_columns_as_number():
    """
    End to end through the adapter: a table whose MAE column is
    declared REAL must reach Grafana as a "number" column, or
    the table panel renders it as unformatted text.
    """

    if not DATABASE.exists():

        pytest.skip("dashboard database not built yet")

    frame = grafana_api.handle_table("table:soh_results")

    columns = {
        column["text"]: column["type"]
        for column in frame["columns"]
    }

    assert columns["test_cell"] == "string"
    assert columns["MAE"] == "number"
    assert columns["R2"] == "number"

    # The values themselves must still be real numbers, not
    # pre-formatted strings.
    first = frame["rows"][0]

    assert isinstance(first[1], float)