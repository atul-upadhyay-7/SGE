"""
Tests for the latency benchmark.

These assert the benchmark is wired to the real artifact and
the real assemble_features path, and that it reports the
stages the README quotes. They deliberately avoid a tight
timing assertion: a machine under load makes those flaky, and
a flaky test is worse than no test. The ceiling here is
generous and only exists to catch an accidental O(n^2) or a
per-call model reload.
"""

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

if str(SRC) not in sys.path:

    sys.path.insert(0, str(SRC))


@pytest.fixture(scope="module")
def measured():

    import benchmark_latency

    return benchmark_latency.measure()


def test_benchmark_reports_every_documented_stage(measured):

    expected = {
        "cold_model_load_ms",
        "warm_predict_1_ms",
        "warm_predict_soh_1_ms",
        "warm_predict_100_ms",
        "warm_predict_1000_ms",
        "end_to_end_cycle_ms",
        "feature_extraction_ms",
    }

    assert expected <= set(measured)


def test_every_measured_value_is_positive(measured):

    for stage, value in measured.items():

        assert value > 0, stage


def test_warm_prediction_is_inside_the_cycle_budget(measured):

    # One cycle is roughly a second in the streaming service.
    # A per-request model reload would blow straight past this,
    # which is the regression worth catching.
    assert measured["warm_predict_soh_1_ms"] < 500


def test_batch_scales_sublinearly_in_pixels(measured):

    # 1000 rows should not cost 1000 times one row; xgboost is
    # vectorised and batches are far cheaper per row.
    single = measured["warm_predict_1_ms"]

    thousand = measured["warm_predict_1000_ms"]

    assert thousand < single * 1000


def test_benchmark_writes_a_report(tmp_path):

    import benchmark_latency

    frame = benchmark_latency.write(
        benchmark_latency.measure()
    )

    assert {"stage", "milliseconds"} == set(frame.columns)

    assert len(frame) == 7

    assert frame["milliseconds"].gt(0).all()