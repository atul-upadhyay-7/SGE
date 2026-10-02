"""
test_data_quality.py
--------------------
The audit's failure modes.

The value of the audit is that it stops the pipeline when the
table is not the table we think it is. These tests pin that
behaviour: a missing file, a missing mandatory column and a
non-numeric mandatory column must each raise, with a message
that names the file, the operation and a fix.
"""

import pandas as pd
import pytest

from data_quality import (
    DataQualityError,
    audit,
    build_report,
)


def _good_frame():

    return pd.DataFrame({
        "cell_id": ["A", "A", "B", "B"],
        "cycle": [1, 2, 1, 2],
        "capacity_ah": [2.0, 1.99, 2.0, 1.97],
        "soh": [100.0, 99.5, 100.0, 98.5],
        "voltage_mean": [3.5, 3.5, 3.6, 3.5],
    })


def test_missing_file_is_an_error(tmp_path):

    with pytest.raises(DataQualityError) as info:

        audit(tmp_path / "absent.csv")

    message = str(info.value)

    assert "does not exist" in message
    assert "load_data.py" in message


@pytest.mark.parametrize("column", [
    "cell_id",
    "cycle",
    "capacity_ah",
    "soh",
])
def test_missing_mandatory_column_is_an_error(
    tmp_path, column
):

    path = tmp_path / "missing.csv"

    _good_frame().drop(columns=[column]).to_csv(
        path, index=False
    )

    with pytest.raises(DataQualityError) as info:

        audit(path)

    assert column in str(info.value)
    assert "missing mandatory columns" in str(info.value)


def test_non_numeric_mandatory_column_is_an_error(tmp_path):

    frame = _good_frame()

    frame["capacity_ah"] = "not-a-number"

    path = tmp_path / "bad_type.csv"

    frame.to_csv(path, index=False)

    with pytest.raises(DataQualityError) as info:

        audit(path)

    message = str(info.value)

    assert "capacity_ah" in message
    assert "not numeric" in message


def test_report_counts_duplicate_cycle_ids(tmp_path):

    frame = _good_frame()

    # A repeated (cell_id, cycle) pair is the schema mistake
    # most likely to inflate a score, so the report has to
    # surface it.
    frame = pd.concat([frame, frame.iloc[[0]]])

    path = tmp_path / "dup.csv"

    frame.to_csv(path, index=False)

    report = audit(path)

    assert report["duplicate_cycle_ids"] == 1


def test_report_flags_capacity_recovery():

    frame = _good_frame()

    # SOH rises from cycle 1 to 2 for cell A: an increase the
    # monotonicity check should count rather than hide.
    frame.loc[1, "capacity_ah"] = 2.05

    report = build_report(frame)

    assert (
        report["monotonicity_capacity"]["A"]
        ["increases_gt_1e-4"] == 1
    )


def test_audit_writes_reports_next_to_the_input(tmp_path):

    # Regression: an earlier version always wrote the reports to
    # the fixed data/processed paths, so auditing a fixture
    # silently overwrote the real data-quality report. The
    # output directory must follow the input.
    frame = _good_frame()

    path = tmp_path / "ml.csv"

    frame.to_csv(path, index=False)

    audit(path)

    assert (tmp_path / "data_quality_report.csv").exists()
    assert (tmp_path / "data_quality_report.json").exists()
