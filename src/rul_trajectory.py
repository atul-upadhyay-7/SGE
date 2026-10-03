"""
rul_trajectory.py
-----------------
Remaining useful life by reference-trajectory matching.

Why this and not a regression model
-----------------------------------
With four cells, a cycle-level regressor for RUL cannot beat a
constant (see train_rul.py and the README). A straight-line
extrapolation of the recent fade rate fails too: NASA capacity
curves are convex with a knee, so a line fitted before the knee
overshoots the end of life by hundreds of cycles.

What does work is asking a simpler question. "Cells that were at
this SOH, how many discharges did they have left?" The estimator
keeps the SOH-versus-discharge curve of every reference cell,
finds where each curve was at the cell's current (smoothed,
predicted) SOH, reads off that reference cell's remaining
discharges to the retirement threshold, and returns the median
over reference cells.

It uses only the cell's own predicted SOH (the leakage-free Mode B
output), never its future, and in evaluation never the test cell's
own curve. Units are discharges, which is what the streaming
tracker counts.

Honest limits
-------------
Four reference cells of one chemistry and one load. A cell that
degrades faster than every reference (B0006 here) is where it
loses to a lifetime baseline. It says where a cell sits on the
average ageing path, not what will happen to that particular cell.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

THRESHOLD = 80.0
SMOOTH_WINDOW = 10
REF_SMOOTH = 5


class TrajectoryRul:

    def __init__(self, curves, threshold=THRESHOLD):
        """
        curves: {cell_id: [soh at discharge 1, 2, ...]} of actual
        SOH from reference cells that reached the threshold.
        """

        self.threshold = float(threshold)
        self.refs = {}

        for cell, soh in curves.items():

            a = pd.Series(np.asarray(soh, dtype=float))
            smooth = (
                a.rolling(REF_SMOOTH, min_periods=1, center=True)
                .median()
                .to_numpy()
            )

            below = smooth <= self.threshold

            if not below.any():

                continue  # never retired: no remaining-life label

            self.refs[cell] = {
                "soh": smooth,
                "eol": int(np.argmax(below)),
            }

    @classmethod
    def from_dataset(cls, df, cells=None, threshold=THRESHOLD):

        cells = cells or sorted(df["cell_id"].unique())

        return cls(
            {
                c: df[df["cell_id"] == c]
                .sort_values("cycle")["soh"]
                .to_numpy()
                for c in cells
            },
            threshold,
        )

    def remaining(self, recent_soh):
        """
        Discharges until the threshold, from recent predicted SOH
        values (most recent last). None without references.
        """

        if not self.refs or len(recent_soh) == 0:

            return None

        s = float(np.median(recent_soh[-SMOOTH_WINDOW:]))

        if s <= self.threshold:

            return 0

        rems = []

        for ref in self.refs.values():

            below = ref["soh"] <= s
            idx = int(np.argmax(below)) if below.any() else ref["eol"]
            rems.append(max(ref["eol"] - idx, 0))

        return int(round(float(np.median(rems))))

    def save(self, path):

        Path(path).parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w") as f:

            json.dump(
                {
                    "threshold": self.threshold,
                    "curves": {
                        c: r["soh"].tolist()
                        for c, r in self.refs.items()
                    },
                },
                f,
            )

    @classmethod
    def load(cls, path):

        path = Path(path)

        if not path.exists():

            return None

        with open(path) as f:

            d = json.load(f)

        return cls(d["curves"], d["threshold"])


def evaluate_loco(df, preds, threshold=THRESHOLD, start=10):
    """
    Leave-one-cell-out. df: ML dataset with actual soh. preds:
    {cell: predicted SOH array} from the LOCO SOH model. Returns a
    DataFrame of per-cell MAE against a fleet-lifetime baseline
    (median reference lifetime minus current discharge).
    """

    cells = sorted(df["cell_id"].unique())
    out = []

    for t in cells:

        refs = [c for c in cells if c != t]
        est = TrajectoryRul.from_dataset(df, refs, threshold)

        act = (
            df[df["cell_id"] == t]
            .sort_values("cycle")["soh"]
            .to_numpy()
        )

        if not (act <= threshold).any():

            continue

        eol = int(np.argmax(act <= threshold))
        pred = np.asarray(preds[t], dtype=float)
        lifetimes = [r["eol"] for r in est.refs.values()]

        err_t, err_f = [], []

        for k in range(start, eol):

            truth = eol - k
            e = est.remaining(pred[: k + 1])
            f = max(np.median(lifetimes) - k, 0)

            if e is None:

                continue

            err_t.append(abs(e - truth))
            err_f.append(abs(f - truth))

        out.append(
            {
                "test_cell": t,
                "eol_discharge": eol,
                "n_points": len(err_t),
                "trajectory_mae": float(np.mean(err_t)),
                "fleet_lifetime_baseline_mae": float(
                    np.mean(err_f)
                ),
            }
        )

    return pd.DataFrame(out)


def main():

    root = Path(__file__).resolve().parent.parent
    proc = root / "data" / "processed"

    df = pd.read_csv(proc / "nasa_ml_dataset.csv")

    preds = {}

    for c in sorted(df["cell_id"].unique()):

        p = pd.read_csv(proc / f"soh_predictions_{c}.csv")
        preds[c] = (
            p.sort_values("cycle")["predicted_soh"].to_numpy()
        )

    res = evaluate_loco(df, preds)
    res.to_csv(proc / "rul_trajectory_results.csv", index=False)

    print(res.to_string(index=False))
    print(
        "\nMean MAE  trajectory: "
        f"{res['trajectory_mae'].mean():.1f}  |  fleet-lifetime "
        f"baseline: {res['fleet_lifetime_baseline_mae'].mean():.1f}"
        " discharges"
    )

    TrajectoryRul.from_dataset(df).save(
        root / "models" / "rul_reference_curves.json"
    )
    print("Saved models/rul_reference_curves.json")


if __name__ == "__main__":
    main()
