"""
conftest.py
-----------
Shared fixtures.

The fixture model is deliberately trivial. It exists to
exercise the loading, feature assembly and latching paths, not
to produce accurate SOH, so it returns a fixed value and the
tests assert on plumbing and timing rather than accuracy.

The committed artifact under models/ is not used as the
fixture, and its loadability is environment-dependent: it was
serialized under a particular XGBoost build, and a
mismatched one fails to deserialize. Tests that need a
working model therefore build a small in-memory one instead,
which keeps the streaming logic under test rather than the
artifact, and keeps the suite runnable offline. The one test
that does care about the real file asserts only that the
probe answers, not that it succeeds.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.dummy import DummyRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline


ROOT = Path(__file__).resolve().parent.parent

# The streaming modules import each other by bare name when run
# as scripts and relatively when imported as a package, so both
# directories have to be importable.
for extra in (ROOT / "src", ROOT / "src" / "streaming"):

    if str(extra) not in sys.path:

        sys.path.insert(0, str(extra))


# The features the trained model actually consumes, in the
# order the bundle stores them: features.FEATURES less
# ambient_temperature, which train_soh.py drops as constant
# across these four cells. Listed explicitly rather than
# derived from features.FEATURES so that a change to the real
# feature set shows up here as a failing suite instead of
# silently redefining what the tests claim to cover.
#
# Note that voltage_range belongs here. It is a genuine model
# input, produced by load_data.build_cycle_features and emitted
# by the tracker, and an earlier version of this list carried
# capacity_ah in its place, which left voltage_range unexercised
# by every streaming test.
ACTIVE_FEATURES = [
    "cycle",
    "voltage_mean",
    "voltage_min",
    "voltage_max",
    "voltage_std",
    "voltage_range",
    "current_mean",
    "current_min",
    "current_max",
    "current_std",
    "temperature_mean",
    "temperature_min",
    "temperature_max",
    "temperature_std",
    "discharge_duration_s",
    "voltage_start",
    "voltage_end",
    "voltage_drop",
    "resistance_proxy_ohm",
    "capacity_change_ah",
    "soh_change_pct",
]


def make_bundle(features=None, value=88.5):
    """
    Build a minimal bundle with the shape predict.py requires.

    The pipeline is a real sklearn Pipeline with a real
    imputer, so feature assembly, column ordering and NaN
    handling are exercised the same way they are in
    production. Only the regressor is trivial.
    """

    features = (
        list(features)
        if features is not None
        else list(ACTIVE_FEATURES)
    )

    pipeline = Pipeline([
        ("imputer", SimpleImputer(
            strategy="median"
        )),
        ("model", DummyRegressor(
            strategy="constant",
            constant=value
        )),
    ])

    # Fit on one synthetic row so the imputer learns medians
    # rather than staying unfitted.
    row = {f: 1.0 for f in features}
    pipeline.fit(pd.DataFrame([row]), [value])

    return {
        "model": pipeline,
        "features": features,
        "target": "soh",
        "trained_on": ["TEST"],
    }


@pytest.fixture
def bundle():

    return make_bundle()


@pytest.fixture
def cycle_row():
    """
    One feature row shaped like a CycleTracker emission.
    """

    row = {
        "cell_id": "TESTCELL",
        "cycle": 1,
        "ambient_temperature": 24.0,
        "voltage_mean": 3.65,
        "voltage_std": 0.21,
        "voltage_min": 2.55,
        "voltage_max": 4.19,
        "voltage_range": 1.64,
        "voltage_start": 4.19,
        "voltage_end": 3.05,
        "voltage_drop": 1.14,
        "resistance_proxy_ohm": 1.08,
        "current_mean": -1.82,
        "current_std": 0.59,
        "current_min": -2.02,
        "current_max": 0.002,
        "temperature_mean": 32.8,
        "temperature_std": 3.85,
        "temperature_min": 28.0,
        "temperature_max": 39.8,
        "discharge_duration_s": 3434.9,
        "capacity_ah": 1.86,
        "capacity_change_ah": np.nan,
        "soh_change_pct": np.nan,
    }

    return row


def discharge_samples(
    n=200,
    current=-2.0,
    start_time=0.0,
    step=3.0,
    voltage=None,
):
    """
    Build a straight-line discharge as per-sample tuples.

    Timestamps advance by `step` seconds, matching this
    dataset's real 2.5 to 22 second cadence rather than
    assuming 1 Hz, so integrated capacity comes out in the
    right range.
    """

    voltage = voltage if voltage is not None else 3.5

    return [
        (
            start_time + i * step,
            voltage - 0.005 * i,
            current,
            30.0 + 0.01 * i
        )
        for i in range(n)
    ]


def feed(tracker, samples):
    """
    Push samples through a tracker, returning emitted rows.
    """

    rows = []

    for timestamp, voltage, current, temperature in samples:

        row = tracker.add_sample(
            timestamp, voltage, current, temperature
        )

        if row is not None:

            rows.append(row)

    return rows
