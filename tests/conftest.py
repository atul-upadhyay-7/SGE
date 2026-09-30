"""
conftest.py
-----------
Shared fixtures.

The committed SOH model cannot be loaded in this environment.
All ten .joblib files in models/ fail to deserialize under the
installed XGBoost 3.4.0, including every version in git
history, so tests that need a working model build a small
in-memory one instead. That keeps the streaming logic under
test rather than the artifact, and keeps the suite runnable
offline.

The fixture model is deliberately trivial. It exists to
exercise the loading, feature assembly and latching paths, not
to produce accurate SOH, so it returns a fixed value and the
tests assert on plumbing and timing rather than accuracy.
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


# The features the trained model actually consumes: the full
# candidate list minus the constant one, which train_soh.py
# drops.
ACTIVE_FEATURES = [
    "voltage_mean",
    "voltage_std",
    "voltage_min",
    "voltage_max",
    "voltage_start",
    "voltage_end",
    "voltage_drop",
    "resistance_proxy_ohm",
    "current_mean",
    "current_std",
    "current_min",
    "current_max",
    "temperature_mean",
    "temperature_std",
    "temperature_min",
    "temperature_max",
    "discharge_duration_s",
    "capacity_ah",
    "capacity_change_ah",
    "soh_change_pct",
    "cycle",
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
