"""Partial-window estimator: feature-contract and B0005 reproducibility.

These are pure-ML tests. They need neither the database nor MQTT, so
scripts/verify.sh runs them with ``--noconftest``; the app-level
conftest imports the full FastAPI app (and its MQTT dependency), which
this test does not exercise.

The B0005 CSV these tests read is an *in-sample regression check, not
accuracy*: the deployable model was trained on every audited cell
including B0005, so reproducing it only proves the estimator runs the
same deterministic pipeline. The held-out number for B0005 lives in
``data/processed/soh_window_heldout_B0005.csv`` and is checked by
``tests/test_window_heldout.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(REPO_ROOT / "src"))

from app.ml import estimator  # noqa: E402

import features  # noqa: E402

CSV = REPO_ROOT / "data" / "processed" / (
    "soh_window_regression_B0005.csv"
)

TOL = 1e-6


def _rows() -> pd.DataFrame:

    return pd.read_csv(CSV)


def test_window_feature_lists_match_offline_pipeline():
    """
    The estimator must not keep a private copy of the feature
    contract; if it ever does, this fails the moment the two differ.
    """

    assert estimator.WINDOW_FEATURES == features.WINDOW_FEATURES
    assert estimator.MODEL_FEATURES == features.WINDOW_MODEL_FEATURES
    assert estimator.WINDOW_UNITS == features.WINDOW_UNITS
    assert (
        estimator.WINDOW_MIN_REFERENCE_DISCHARGES
        == features.WINDOW_MIN_REFERENCE_DISCHARGES
    )
    assert estimator.WindowRangeError is features.WindowRangeError
    assert (
        estimator.OUT_OF_TRAINED_RANGE == features.OUT_OF_TRAINED_RANGE
    )
    # The refusal is a user-facing message, so pin its spelling.
    assert features.OUT_OF_TRAINED_RANGE == "out of trained range"


def test_predict_soh_matches_saved_csv_for_b0005():
    """
    In-sample regression check, not accuracy. Every B0005 discharge,
    scored from the first five discharges as the reference, must
    reproduce the saved prediction to within 1e-6.
    """

    rows = _rows()

    early = rows.head(features.WINDOW_REFERENCE_WINDOW)

    worst = 0.0

    for _, row in rows.iterrows():

        got = estimator.predict_soh(row, early)

        worst = max(
            worst, abs(got - float(row["predicted_soh"]))
        )

    assert worst <= TOL, (
        f"worst B0005 deviation {worst:.3e} exceeds {TOL}"
    )


def test_fewer_than_three_early_discharges_is_an_explicit_error():

    rows = _rows()

    with pytest.raises(estimator.InsufficientReferenceError):

        estimator.predict_soh(rows.iloc[10], rows.head(2))


def test_exactly_three_early_discharges_is_allowed():

    rows = _rows()

    value = estimator.predict_soh(rows.iloc[10], rows.head(3))

    assert value == value  # not NaN
    assert 0.0 < value < 120.0


@pytest.mark.parametrize("mean_voltage", [2.5, 4.4])
def test_input_outside_the_trained_window_is_refused(mean_voltage):
    """
    The model was trained only on the 4.0-3.4 V window. A window
    whose mean voltage is outside that band has no training data, so
    the estimator must refuse it with a clear error rather than
    impute and return a plausible-looking number.
    """

    rows = _rows()

    early = rows.head(features.WINDOW_REFERENCE_WINDOW)

    out_of_band = dict(rows.iloc[10])

    out_of_band["vw_voltage_mean"] = mean_voltage

    with pytest.raises(estimator.WindowRangeError) as excinfo:

        estimator.predict_soh(out_of_band, early)

    message = str(excinfo.value)

    assert "window" in message.lower()
    assert f"{mean_voltage}" in message


def test_input_at_three_times_the_training_maximum_is_refused():
    """
    PHASE 3: the estimator input is range-checked against the model's
    own training range. A discharge whose capacity-per-amp stands at
    three times the training maximum must come back as the refusal
    sentinel, not a scored number, because the model has no basis
    outside the range it was trained across.
    """

    import json

    meta = json.loads(
        (REPO_ROOT / "models" / "soh_window_all.json").read_text(
            encoding="utf-8"
        )
    )

    high = float(
        meta["feature_ranges"]["vw_capacity_per_amp"][1]
    )

    rows = _rows()

    early = rows.head(features.WINDOW_REFERENCE_WINDOW)

    row = dict(rows.iloc[10])

    current = abs(float(row["current_mean"]))

    row["vw_capacity_ah"] = 3.0 * high * current

    got = estimator.predict_soh(row, early)

    assert got == features.OUT_OF_TRAINED_RANGE
