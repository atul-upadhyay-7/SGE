"""
test_window_heldout.py
----------------------
The B0005 number the project reports is the held-out one, and this
test refuses to let the in-sample error stand in for it.

``data/processed/soh_window_regression_B0005.csv`` is an in-sample
regression check: the deployable model trained on B0005, so its error
there only proves the pipeline is deterministic.

``data/processed/soh_window_heldout_B0005.csv`` comes from the same
feature contract trained without B0005 (the other G1 cells), so its
per-discharge error is the report's leave-one-cell-out: G1 row. These
tests pin the two to the same number.
"""

import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

PROC = ROOT / "data" / "processed"

HELDOUT_CSV = PROC / "soh_window_heldout_B0005.csv"
REGRESSION_CSV = PROC / "soh_window_regression_B0005.csv"
EXPERIMENT_CSV = PROC / "experiments" / "soh_partial_transfer.csv"

MODEL_DIR = ROOT / "models"

RESULTS = ROOT / "docs" / "RESULTS.md"

TOL = 0.01

FIXTURE_CELL = "B0005"
PROTOCOL = "leave-one-cell-out: G1"


def _heldout_mae(column):

    rows = pd.read_csv(HELDOUT_CSV)

    return float((rows["soh"] - rows[column]).abs().mean())


def _experiment_mae(model):

    rows = pd.read_csv(EXPERIMENT_CSV)

    hit = rows[
        (rows["protocol"] == PROTOCOL)
        & (rows["test_cell"] == FIXTURE_CELL)
        & (rows["model"] == model)
    ]

    return float(hit["mae_pct"].iloc[0])


def _in_sample_mae():

    rows = pd.read_csv(REGRESSION_CSV)

    return float((rows["soh"] - rows["predicted_soh"]).abs().mean())


def test_heldout_predictions_cover_only_b0005():
    """The held-out artifact is B0005 and only B0005, one row per
    discharge, so its mean error is that cell's error alone."""

    rows = pd.read_csv(HELDOUT_CSV)

    assert set(rows["cell_id"].unique()) == {FIXTURE_CELL}
    assert len(rows) == 168


def test_heldout_mae_matches_the_reported_number():
    """
    The held-out model's B0005 error must equal the report's
    leave-one-cell-out: G1 row to within 0.01%. If the artifact and
    the reported number ever drift apart, this fails.
    """

    assert (
        abs(
            _heldout_mae("predicted_elasticnet")
            - _experiment_mae("ElasticNet")
        )
        <= TOL
    )
    assert (
        abs(
            _heldout_mae("predicted_xgboost")
            - _experiment_mae("XGBoost")
        )
        <= TOL
    )


def test_heldout_model_never_saw_b0005():
    """The artifact that supplies the held-out number must genuinely
    exclude the fixture cell from training."""

    meta = json.loads(
        (MODEL_DIR / "soh_window_no_B0005.json").read_text()
    )

    assert FIXTURE_CELL not in meta["trained_on"]
    assert meta["trained_on"] == sorted(meta["trained_on"])
    assert meta["trained_on"]


def test_deployable_model_is_in_sample_on_b0005():
    """
    The all-cells model did train on B0005 — which is exactly why its
    B0005 error is a regression check and not an accuracy number. The
    in-sample error is optimistic by construction.
    """

    meta = json.loads(
        (MODEL_DIR / "soh_window_all.json").read_text()
    )

    assert FIXTURE_CELL in meta["trained_on"]

    assert _in_sample_mae() < _heldout_mae("predicted_xgboost")


def test_readme_does_not_quote_the_in_sample_error():
    """
    docs/RESULTS.md reports the held-out number only. It must not quote
    the in-sample B0005 error as if it were accuracy.
    """

    text = RESULTS.read_text()

    token = f"{_in_sample_mae():.2f}"

    assert re.search(rf"(?<![\d.]){re.escape(token)}(?![\d])", text) is None
