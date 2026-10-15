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

    def remaining_distribution(self, recent_soh):
        """
        Remaining discharges predicted by EVERY reference cell, from
        recent predicted SOH values (most recent last). None without
        references. The spread of these values across references is
        the honest prediction interval: it says how much the answer
        depends on which reference curve you happen to match.
        """

        if not self.refs or len(recent_soh) == 0:

            return None

        s = float(np.median(recent_soh[-SMOOTH_WINDOW:]))

        if s <= self.threshold:

            return np.zeros(1, dtype=float)

        rems = []

        for ref in self.refs.values():

            below = ref["soh"] <= s
            idx = int(np.argmax(below)) if below.any() else ref["eol"]
            rems.append(max(ref["eol"] - idx, 0))

        return np.asarray(rems, dtype=float)

    def remaining(self, recent_soh):
        """
        Discharges until the threshold, from recent predicted SOH
        values (most recent last). None without references. This is
        the median of remaining_distribution(), so the point estimate
        and the interval cannot drift apart.
        """

        dist = self.remaining_distribution(recent_soh)

        if dist is None:

            return None

        return int(round(float(np.median(dist))))

    def remaining_interval(self, recent_soh):
        """
        (p10, p50, p90) of the per-reference remaining discharges.
        Uses linear interpolation on the small reference sample, so a
        four-reference estimate yields a p10/p90 that is wider than
        the observed min/max rather than collapsing onto it. None
        without references. p50 equals remaining().
        """

        dist = self.remaining_distribution(recent_soh)

        if dist is None:

            return None

        p10, p50, p90 = np.percentile(dist, [10.0, 50.0, 90.0])

        return float(p10), float(p50), float(p90)

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


def evaluate_loco(df, preds, threshold=THRESHOLD, start=10, cells=None):
    """
    Leave-one-cell-out. df: ML dataset with actual soh. preds:
    {cell: predicted SOH array} from the LOCO SOH model. Returns a
    DataFrame of per-cell MAE against a fleet-lifetime baseline
    (median reference lifetime minus current discharge).
    """

    cells = sorted(cells) if cells is not None else sorted(df["cell_id"].unique())
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
        if t not in preds:
            continue
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


def prediction_bands(df, preds, cells=None, threshold=THRESHOLD, start=10):
    """
    Per-cycle RUL point estimate with a real prediction interval.

    For each held-out cell the reference set is the OTHER cells, so
    the band that ends up on the dashboard is produced by exactly
    the leave-one-cell-out protocol the reported MAE is measured
    under. Columns: cell_id, cycle, predicted_rul, rul_p10, rul_p90.
    """

    cells = sorted(cells) if cells is not None else sorted(df["cell_id"].unique())
    out = []

    for t in cells:

        if t not in preds:

            continue

        frame = preds[t]

        # Only in-service discharges are a prediction: after the cell
        # reaches the retirement threshold the remaining life is 0 by
        # definition, and averaging those zeros would make the latest
        # fleet estimate meaningless.
        if "is_pre_eol" in frame.columns:

            frame = frame[frame["is_pre_eol"].astype(bool)]

        refs = [c for c in cells if c != t]
        est = TrajectoryRul.from_dataset(df, refs, threshold)

        cycles = frame["cycle"].to_numpy()
        pred = frame["predicted_soh"].to_numpy(dtype=float)

        for k in range(len(pred)):

            if k + 1 < start:

                continue

            interval = est.remaining_interval(pred[: k + 1])

            if interval is None:

                continue

            p10, p50, p90 = interval

            out.append(
                {
                    "cell_id": t,
                    "cycle": int(cycles[k]),
                    "predicted_rul": p50,
                    "rul_p10": p10,
                    "rul_p90": p90,
                }
            )

    return pd.DataFrame(out)


def main():

    root = Path(__file__).resolve().parent.parent
    proc = root / "data" / "processed"

    df = pd.read_csv(proc / "nasa_ml_dataset_clean.csv")

    preds = {}

    for c in sorted(df["cell_id"].unique()):

        pfile = proc / f"soh_predictions_{c}.csv"
        if not pfile.exists():
            continue
        preds[c] = (
            pd.read_csv(pfile).sort_values("cycle")
        )

    cells = sorted(df["cell_id"].unique())
    res = evaluate_loco(
        df,
        {c: p["predicted_soh"].to_numpy() for c, p in preds.items()},
        cells=cells,
    )
    res.to_csv(proc / "rul_trajectory_results.csv", index=False)

    bands = prediction_bands(df, preds, cells=cells)
    bands.to_csv(proc / "rul_prediction_bands.csv", index=False)

    if not bands.empty:
        print(
            f"\nWrote rul_prediction_bands.csv: {len(bands)} rows, "
            f"interval width mean "
            f"{(bands['rul_p90'] - bands['rul_p10']).mean():.1f} "
            "discharges"
        )

    if not res.empty:
        print(res.to_string(index=False))
        print(
            "\nMean MAE  trajectory: "
            f"{res['trajectory_mae'].mean():.1f}  |  fleet-lifetime "
            f"baseline: {res['fleet_lifetime_baseline_mae'].mean():.1f}"
            " discharges"
        )
    else:
        print("No results produced")

    TrajectoryRul.from_dataset(df, cells=cells).save(
        root / "models" / "rul_reference_curves.json"
    )
    print("Saved models/rul_reference_curves.json")


if __name__ == "__main__":
    main()
