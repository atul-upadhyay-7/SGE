"""
causal.py
---------
Causal (past-only) features for the SOH and RUL models.

The mentor asked the model to cover four things: the load on
the battery, the last load, the change in capacity and the
change in other parameters. This module builds all four from
information a battery management system has at the moment a
discharge cycle closes. Nothing here looks at a later cycle,
and nothing is derived from the SOH target of the cycle being
predicted.

    load_a                 mean discharge current of this cycle
                           (positive amps drawn from the cell)
    last_load_a            mean discharge current of the previous
                           cycle
    delta_load_a           load_a - last_load_a
    prev_capacity_change_ah
                           capacity(k-1) - capacity(k-2). Both
                           discharges are already finished when
                           cycle k closes, so this is past-only.
                           It is NOT the change that includes
                           cycle k, which was removed as leakage.
    d_<param>              this cycle minus the previous cycle,
                           for the other measured parameters
    sl_<param>             least-squares slope over the trailing
                           window of cycles, including this one
                           (the explicit fade-rate style input)

One code path serves both worlds. Offline training calls
add_causal_features(), which replays every cell through a
CausalFeatureTracker. The streaming service owns one tracker per
cell and calls update() as each cycle closes. Because training
and serving run the same class, they cannot drift apart.
"""

from collections import deque

import numpy as np

# measured per-cycle parameters that get a delta and a slope
OTHER_PARAMETERS = [
    "voltage_mean",
    "voltage_end",
    "voltage_drop",
    "discharge_duration_s",
    "resistance_proxy_ohm",
    "temperature_mean",
]

WINDOW = 10
MIN_SLOPE_POINTS = 3

LOAD_FEATURES = [
    "load_a",
    "last_load_a",
    "delta_load_a",
]

CAPACITY_FEATURES = ["prev_capacity_change_ah"]

DELTA_FEATURES = [f"d_{p}" for p in OTHER_PARAMETERS]

SLOPE_FEATURES = [f"sl_{p}" for p in OTHER_PARAMETERS]

CAUSAL_FEATURES = (
    LOAD_FEATURES
    + CAPACITY_FEATURES
    + DELTA_FEATURES
    + SLOPE_FEATURES
)


def _slope(values):
    """Least-squares slope of values against 0..n-1."""

    n = len(values)

    if n < MIN_SLOPE_POINTS:

        return np.nan

    y = np.asarray(values, dtype=float)

    if not np.all(np.isfinite(y)):

        return np.nan

    x = np.arange(n, dtype=float)
    x -= x.mean()

    return float((x * (y - y.mean())).sum() / (x * x).sum())


class CausalFeatureTracker:
    """
    Per-cell memory of past cycles.

    update(row) takes the feature row of the cycle that just
    closed, returns the causal features for it, and only then
    remembers it. The order matters: a feature can never see
    the cycle after its own.
    """

    def __init__(self, window=WINDOW):

        self.window = int(window)
        self.history = {
            p: deque(maxlen=self.window)
            for p in OTHER_PARAMETERS
        }
        self.loads = deque(maxlen=1)
        self.capacities = deque(maxlen=2)
        self.discharge_index = 0

    def reset(self):

        self.__init__(self.window)

    def update(self, row):

        self.discharge_index += 1

        out = {"discharge_index": self.discharge_index}

        load = -float(row["current_mean"])
        last_load = self.loads[0] if self.loads else np.nan

        out["load_a"] = load
        out["last_load_a"] = last_load
        out["delta_load_a"] = load - last_load

        if len(self.capacities) == 2:

            out["prev_capacity_change_ah"] = (
                self.capacities[1] - self.capacities[0]
            )

        else:

            out["prev_capacity_change_ah"] = np.nan

        for name in OTHER_PARAMETERS:

            value = float(row[name])
            past = self.history[name]

            out[f"d_{name}"] = (
                value - past[-1] if past else np.nan
            )

            past.append(value)

            out[f"sl_{name}"] = _slope(list(past))

        self.loads.append(load)

        capacity = row.get("capacity_ah")

        if capacity is not None and np.isfinite(capacity):

            self.capacities.append(float(capacity))

        return out


def add_causal_features(df, window=WINDOW):
    """
    Add CAUSAL_FEATURES and discharge_index to a cycle table.

    Rows are replayed per cell in cycle order through the same
    CausalFeatureTracker the streaming service uses.
    """

    ordered = df.sort_values(
        ["cell_id", "cycle"]
    ).reset_index(drop=True)

    frames = []

    for _, cell in ordered.groupby("cell_id", sort=False):

        tracker = CausalFeatureTracker(window)

        extras = [
            tracker.update(row)
            for row in cell.to_dict("records")
        ]

        frames.append(
            ordered.loc[cell.index].assign(
                **{
                    key: [e[key] for e in extras]
                    for key in ["discharge_index"]
                    + CAUSAL_FEATURES
                }
            )
        )

    import pandas as pd

    return pd.concat(frames).reset_index(drop=True)
