"""
test_window_model_features.py
-----------------------------
Guard the partial-window feature contract shared between the offline
pipeline and the backend estimator.

There must be exactly one definition of the window feature lists (in
src/features.py), and the vectorised offline builder
(run_experiments.cell_relative_features) must produce the same values
as the scalar inference builder (features.window_model_row). If either
drifts, the backend would score the model on inputs it never saw.
"""

from pathlib import Path

import pandas as pd
import pytest

import features
import run_experiments


ROOT = Path(__file__).resolve().parent.parent

CLEAN = ROOT / "data" / "processed" / "nasa_ml_dataset_clean.csv"

FIXTURE_CELL = "B0005"


def test_run_experiments_shares_the_window_lists():
    """run_experiments must import the lists, not redeclare them."""

    assert list(run_experiments.VW_FEATURES) == list(
        features.WINDOW_FEATURES
    )
    assert list(run_experiments.WINDOW_NORM_FEATURES) == list(
        features.WINDOW_MODEL_FEATURES
    )


def test_window_model_row_matches_vectorised_features():
    """
    The scalar builder the estimator calls must agree with the
    vectorised builder the trainer fits on, cell-row for cell-row.
    """

    df = pd.read_csv(CLEAN)

    data = run_experiments.cell_relative_features(df)

    cell = df[df["cell_id"] == FIXTURE_CELL].sort_values(
        "discharge_index"
    )

    early = [
        df.loc[index].to_dict()
        for index in cell.head(
            features.WINDOW_REFERENCE_WINDOW
        ).index
    ]

    worst = 0.0

    for index, _ in cell.iterrows():

        built = features.window_model_row(df.loc[index], early)

        for name in features.WINDOW_MODEL_FEATURES:

            worst = max(
                worst,
                abs(built[name] - float(data.loc[index, name])),
            )

    assert worst < 1e-9, (
        f"window_model_row deviates from cell_relative_features "
        f"by {worst:.3e}"
    )


def test_window_model_row_refuses_a_window_outside_the_trained_band():
    """
    The window features are defined between 4.0 and 3.4 V. A window
    whose mean voltage is outside that band was measured over a
    different span, so the shared builder must refuse it — the same
    error class the backend estimator raises — rather than build
    ratios from an input the model has never seen.
    """

    df = pd.read_csv(CLEAN)

    cell = df[df["cell_id"] == FIXTURE_CELL].sort_values(
        "discharge_index"
    )

    early = [
        df.loc[index].to_dict()
        for index in cell.head(
            features.WINDOW_REFERENCE_WINDOW
        ).index
    ]

    good = df.loc[cell.index[10]].to_dict()

    # A valid measurement passes.
    built = features.window_model_row(good, early)
    assert set(built) == set(features.WINDOW_MODEL_FEATURES)

    for bad_voltage in (2.5, 4.4):

        bad = dict(good)
        bad["vw_voltage_mean"] = bad_voltage

        with pytest.raises(features.WindowRangeError):

            features.window_model_row(bad, early)


def test_window_band_matches_the_extractor():
    """The guard checks the same band the extractor cut the window
    from; if the two constants ever diverge the model would reject
    its own training window."""

    import load_data

    assert (
        load_data.VOLTAGE_WINDOW_HIGH == features.WINDOW_VOLTAGE_HIGH
    )
    assert load_data.VOLTAGE_WINDOW_LOW == features.WINDOW_VOLTAGE_LOW
