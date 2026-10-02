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