"""Causal features: past-only, and identical offline vs streaming."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from causal import (  # noqa: E402
    CAUSAL_FEATURES,
    CausalFeatureTracker,
    add_causal_features,
)


def _frame(n=30):

    rng = np.random.default_rng(0)
    k = np.arange(1, n + 1)

    return pd.DataFrame(
        {
            "cell_id": "X",
            "cycle": k,
            "current_mean": -1.8 + 0.01 * rng.standard_normal(n),
            "voltage_mean": 3.5 - 0.002 * k,
            "voltage_end": 2.7 - 0.001 * k,
            "voltage_drop": 1.4 + 0.001 * k,
            "discharge_duration_s": 3500 - 5.0 * k,
            "resistance_proxy_ohm": 0.2 + 0.001 * k,
            "temperature_mean": 30 + 0.01 * k,
            "capacity_ah": 2.0 - 0.003 * k,
        }
    )


def test_offline_equals_streaming_tracker():

    df = _frame()
    offline = add_causal_features(df)

    tracker = CausalFeatureTracker()

    for i, row in enumerate(df.to_dict("records")):

        live = tracker.update(row)

        for name in CAUSAL_FEATURES:

            a = offline.loc[i, name]
            b = live[name]

            assert (np.isnan(a) and np.isnan(b)) or np.isclose(a, b)

        assert live["discharge_index"] == offline.loc[i, "discharge_index"]


def test_features_ignore_the_future():

    df = _frame()
    full = add_causal_features(df)
    cut = add_causal_features(df.iloc[:15])

    for name in CAUSAL_FEATURES:

        a = full.loc[:14, name].to_numpy()
        b = cut[name].to_numpy()

        assert np.allclose(a, b, equal_nan=True)


def test_load_and_last_load():

    tracker = CausalFeatureTracker()
    base = _frame(3).to_dict("records")
    base[0]["current_mean"] = -1.0
    base[1]["current_mean"] = -2.0

    first = tracker.update(base[0])
    second = tracker.update(base[1])

    assert first["load_a"] == 1.0
    assert np.isnan(first["last_load_a"])
    assert second["last_load_a"] == 1.0
    assert second["delta_load_a"] == 1.0


def test_prev_capacity_change_excludes_current_cycle():

    tracker = CausalFeatureTracker()
    rows = _frame(4).to_dict("records")
    caps = [2.0, 1.9, 1.7, 0.1]  # current cycle can be anything

    out = []

    for row, cap in zip(rows, caps):

        row["capacity_ah"] = cap
        out.append(tracker.update(row))

    assert np.isnan(out[1]["prev_capacity_change_ah"])
    assert np.isclose(out[2]["prev_capacity_change_ah"], -0.1)
    # the 0.1 Ah collapse at cycle 4 is invisible to cycle 4
    assert np.isclose(out[3]["prev_capacity_change_ah"], -0.2)


def test_slope_recovers_linear_trend():

    out = add_causal_features(_frame())

    assert np.isclose(
        out["sl_discharge_duration_s"].iloc[-1], -5.0
    )
