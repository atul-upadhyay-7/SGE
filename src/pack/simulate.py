"""
simulate.py
-----------
Build 3S pack scenarios from the NASA cells and inject faults.

There is no faulted hardware in this project, so fault detection
cannot be validated on real faults. What can be done honestly:

  * Healthy scenarios use real NASA cycles from three different
    cells run side by side as a pack. Their natural differences in
    capacity and fade rate are real, so they exercise the
    imbalance logic without any injection.
  * Fault scenarios inject a named, synthetic disturbance into one
    cell of a healthy scenario. Every such scenario carries
    fault=True and the cycle at which the disturbance starts, so a
    report can state detection delay and missed faults, and can
    state that the faults are synthetic.

Injections
----------
resistance_spike   resistance x2.2 from start (contact or cell
                   damage); also sags end-of-discharge voltage by
                   a resistive drop at the cell's load
cell_sag           end-of-discharge voltage pulled 120 mV down
                   (weak or mismatched cell, loose tap)
thermal            peak temperature climbs 2 C per cycle from
                   start up to +35 C, with a rate flag on the last
                   steps (a slow thermal drift, not a runaway)
"""

import numpy as np
import pandas as pd

DEFAULT_PACK = ("B0005", "B0007", "B0018")

FAULTS = ("resistance_spike", "cell_sag", "thermal")


def pack_frames(df, cells=DEFAULT_PACK, soh_col="soh"):
    """
    Per-cell frames indexed by discharge ordinal with the fields
    the monitor wants.
    """

    out = {}

    for cell in cells:

        d = (
            df[df["cell_id"] == cell]
            .sort_values("cycle")
            .reset_index(drop=True)
        )

        out[cell] = pd.DataFrame(
            {
                "cycle": np.arange(1, len(d) + 1),
                "soh": d[soh_col].to_numpy(),
                "resistance_ohm": d[
                    "resistance_proxy_ohm"
                ].to_numpy(),
                "v_end": d["voltage_end"].to_numpy(),
                "temp_max": d["temperature_max"].to_numpy(),
                "load_a": -d["current_mean"].to_numpy(),
            }
        )

    return out


def inject(frames, cell, kind, start):
    """Return a copy of frames with a synthetic fault in one cell."""

    if kind not in FAULTS:

        raise ValueError(f"unknown fault {kind!r}")

    frames = {c: f.copy() for c, f in frames.items()}
    f = frames[cell]
    mask = f["cycle"] >= start
    k = (f["cycle"] - start + 1).clip(lower=0)

    if kind == "resistance_spike":

        f.loc[mask, "resistance_ohm"] *= 2.2
        f.loc[mask, "v_end"] -= (
            0.2 * f.loc[mask, "load_a"]
        )

    elif kind == "cell_sag":

        f.loc[mask, "v_end"] -= 0.12

    elif kind == "thermal":

        f.loc[mask, "temp_max"] += np.minimum(
            2.0 * k[mask], 35.0
        )

        f["dtdt_c_per_s"] = 0.0

        # a rate flag on the last steps of the climb
        late = k >= 14
        f.loc[late, "dtdt_c_per_s"] = 1.2

    frames[cell] = f

    return frames


def replay(frames, monitor):
    """
    Feed frames to a PackMonitor cycle by cycle (all cells at the
    same discharge ordinal, then evaluate). Yields (cycle, report).
    """

    horizon = min(len(f) for f in frames.values())

    for i in range(horizon):

        for cell, f in frames.items():

            row = f.iloc[i].to_dict()
            monitor.update(cell, row)

        yield i + 1, monitor.evaluate()
