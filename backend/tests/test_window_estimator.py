"""Partial-window estimator: feature-contract and B0005 reproducibility.

These are pure-ML tests. They need neither the database nor MQTT, so
scripts/verify.sh runs them with ``--noconftest``; the app-level
conftest imports the full FastAPI app (and its MQTT dependency), which
this test does not exercise.
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
    "soh_window_predictions_B0005.csv"
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


def test_predict_soh_matches_saved_csv_for_b0005():
    """
    Every B0005 discharge, scored from the first five discharges as the
    reference, must reproduce the saved prediction to within 1e-6.
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
