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


BUNDLE_JSON = ROOT / "models" / "soh_window_all.json"


def _bundle_ranges():
    """The per-feature [min, max] the deployable model records."""

    import json

    meta = json.loads(BUNDLE_JSON.read_text(encoding="utf-8"))

    return meta["feature_ranges"]


def test_bundle_records_the_training_range_of_every_model_input():
    """
    The estimator refuses inputs outside the model's own training
    range, so that range has to travel in the bundle. Each recorded
    [min, max] must be the range of that feature over the audited
    cells the model was trained on.
    """

    ranges = _bundle_ranges()

    assert set(ranges) == set(features.WINDOW_MODEL_FEATURES)

    df = pd.read_csv(CLEAN)

    data = run_experiments.cell_relative_features(df)

    for name, (low, high) in ranges.items():

        assert low <= high

        assert low == pytest.approx(float(data[name].min()))
        assert high == pytest.approx(float(data[name].max()))


def test_the_allowed_range_is_the_training_range_widened_by_the_slack():
    """PHASE 3: the guard widens the training [min, max] by
    WINDOW_RANGE_SLACK of its span on each side, so a value a little
    past the training maximum is still scored but a value far past it
    is not."""

    low, high = 1.0, 2.0

    span = high - low

    got_low, got_high = features.widen_trained_range(low, high)

    assert got_low == pytest.approx(
        low - features.WINDOW_RANGE_SLACK * span
    )
    assert got_high == pytest.approx(
        high + features.WINDOW_RANGE_SLACK * span
    )

    ranges = {"f": (low, high)}

    just_past = high + 0.5 * features.WINDOW_RANGE_SLACK * span
    assert features.out_of_range_features({"f": just_past}, ranges) == {}

    well_past = high + 2.0 * features.WINDOW_RANGE_SLACK * span
    flagged = features.out_of_range_features({"f": well_past}, ranges)

    assert flagged["f"][0] == pytest.approx(well_past)


def test_three_times_the_training_maximum_is_out_of_range():
    """PHASE 3: a feature set at three times the training maximum is
    outside the widened band, so the guard names every input."""

    ranges = _bundle_ranges()

    inputs = {
        name: 3.0 * bounds[1] for name, bounds in ranges.items()
    }

    flagged = features.out_of_range_features(inputs, ranges)

    assert set(flagged) == set(features.WINDOW_MODEL_FEATURES)

