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

from preprocessing import (  # noqa: E402
    EOL_SOH_THRESHOLD,
    first_eol_discharge,
)
from repro import rounded  # noqa: E402

# One retirement threshold for the whole project, defined in
# preprocessing so this module and the cleaned dataset cannot use
# different numbers.
THRESHOLD = EOL_SOH_THRESHOLD
SMOOTH_WINDOW = 10
REF_SMOOTH = 5

# Opening discharges used by the fade-rate extrapolation baseline.
# The prompt's "first 20-30 discharges" is this window; 20 is the
# best of 20/25/30 on the four held-out cells (see CHANGES, Step 16).
EXTRAP_WINDOW = 20


def _first_eol_position(soh, threshold=THRESHOLD):
    """
    0-based position of the first discharge below the retirement
    threshold, or None.

    The reported end-of-life discharge is this position plus 1 — the
    same number preprocessing.first_eol_discharge returns and every
    table stores. The position is kept for the per-discharge error
    loops, which index arrays directly. The two used to disagree:
    this module reported the raw position, so B0005 read 106 here and
    107 in every other table.
    """

    eol = first_eol_discharge(soh, threshold=threshold)

    return None if eol is None else eol - 1


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

            # This reference-curve position is a modelling quantity,
            # not a reported end of life: it is read off the *smoothed*
            # curve and used only as a 0-based position in the
            # "how many discharges did this reference have left"
            # subtraction below. The end of life every table reports is
            # first_eol_discharge on the measured SOH.
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
        the reference spread: it says how much the answer depends on
        which reference curve you happen to match. It is not a
        calibrated predictive interval, and its empirical coverage
        is measured in evaluate_loco rather than assumed.
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

        eol = _first_eol_position(act, threshold)

        if eol is None:

            continue

        eol_discharge = eol + 1
        if t not in preds:
            continue
        pred = np.asarray(preds[t], dtype=float)
        lifetimes = [r["eol"] for r in est.refs.values()]

        err_t, err_f = [], []
        inside, widths = 0, []

        for k in range(start, eol):

            truth = eol - k

            dist = est.remaining_distribution(pred[: k + 1])

            # Same point estimate TrajectoryRul.remaining() returns, so
            # the reported MAE is untouched by the coverage bookkeeping.
            e = (
                None
                if dist is None
                else int(round(float(np.median(dist))))
            )
            f = max(np.median(lifetimes) - k, 0)

            if e is None:

                continue

            err_t.append(abs(e - truth))
            err_f.append(abs(f - truth))

            # Empirical coverage of the 10th/90th reference spread
            # against the real remaining life: does the interval that
            # ships on the dashboard actually contain the truth? It is
            # a reference spread, not a calibrated band, so this number
            # is expected to miss the nominal ~80%.
            p10, p90 = np.percentile(dist, [10.0, 90.0])

            widths.append(p90 - p10)

            if p10 <= truth <= p90:

                inside += 1

        n = len(err_t)

        out.append(
            {
                "test_cell": t,
                "eol_discharge": eol_discharge,
                "n_points": n,
                "trajectory_mae": float(np.mean(err_t)),
                "fleet_lifetime_baseline_mae": float(
                    np.mean(err_f)
                ),
                "band_coverage_pct": (
                    100.0 * float(inside) / n if n else float("nan")
                ),
                "band_width_mean": (
                    float(np.mean(widths)) if widths else float("nan")
                ),
            }
        )

    return pd.DataFrame(out)


def prediction_bands(df, preds, cells=None, threshold=THRESHOLD, start=10):
    """
    Per-cycle RUL point estimate with the reference spread.

    For each held-out cell the reference set is the OTHER cells, so
    the spread that ends up on the dashboard is produced by exactly
    the leave-one-cell-out protocol the reported MAE is measured
    under. It is a reference spread, not a calibrated interval.
    Columns: cell_id, cycle, predicted_rul, rul_p10, rul_p90.
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


def _traj_points(df, test_cell, pred, refs, threshold, start):
    """
    Per-discharge (truth, point-estimate) pairs for one held-out
    cell, plus its end-of-life discharge. Shared by the trajectory
    MAE and the calibration experiment so the point estimate that is
    scored and the one that is calibrated cannot drift apart.
    """

    est = TrajectoryRul.from_dataset(df, refs, threshold)

    act = df[df["cell_id"] == test_cell].sort_values("cycle")["soh"].to_numpy()

    eol = _first_eol_position(act, threshold)

    if eol is None:

        return [], None

    pred = np.asarray(pred, dtype=float)

    points = []

    for k in range(start, eol):

        truth = eol - k
        dist = est.remaining_distribution(pred[: k + 1])

        if dist is None:

            continue

        points.append((truth, int(round(float(np.median(dist))))))

    return points, eol


def _implied_eol(y, threshold, window):
    """
    End-of-life discharge implied by a straight line fitted through
    the first `window` measured SOH values, or None when the line is
    flat or rising (no threshold crossing ahead). Shared by the
    scored extrapolation and the dashboard series so the estimate
    that is scored and the one that is drawn cannot drift apart.
    """

    y = np.asarray(y, dtype=float)
    n = min(window, len(y))

    slope, intercept = np.polyfit(np.arange(n), y[:n], 1)

    if slope >= 0:

        return None

    return float((threshold - intercept) / slope)


def extrapolation_loco(
    df,
    threshold=THRESHOLD,
    start=10,
    window=EXTRAP_WINDOW,
):
    """
    Fade-rate extrapolation baseline for RUL.

    A cell is described by a straight line fitted through the first
    `window` measured SOH values, extrapolated to the retirement
    threshold. The implied end-of-life discharge is then a single
    number and RUL at discharge k is implied_eol - k. It uses only
    the cell's own measured capacity so far (observable at cycle t,
    no future, not the target), which is the same quantity a lab or
    full-discharge BMS has.

    Unlike trajectory matching this can follow a cell that fades
    faster than every reference (B0006), but NASA capacity curves
    are convex, so on a cell with a late knee the early line is too
    shallow and the extrapolation overshoots. Both cases are
    reported.
    """

    out = []

    for t in sorted(df["cell_id"].unique()):

        y = (
            df[df["cell_id"] == t]
            .sort_values("cycle")["soh"]
            .to_numpy(dtype=float)
        )

        eol = _first_eol_position(y, threshold)

        if eol is None:

            continue

        implied = _implied_eol(y, threshold, window)

        if implied is None:

            continue

        errs = [
            abs(max(implied - k, 0.0) - (eol - k))
            for k in range(start, eol)
        ]

        if not errs:

            continue

        out.append({
            "test_cell": t,
            "eol_discharge": eol + 1,
            "n_points": len(errs),
            # _implied_eol crosses the fit on the 0-based discharge
            # axis, so +1 puts it on the same 1-based discharge scale
            # as eol_discharge.
            "implied_eol": float(implied) + 1.0,
            "extrapolation_mae": float(np.mean(errs)),
        })

    return pd.DataFrame(out)


def extrapolation_bands(
    df,
    cells=None,
    threshold=THRESHOLD,
    start=10,
    window=EXTRAP_WINDOW,
):
    """
    Per-cycle RUL from the fade-rate extrapolation, for the dashboard.

    This is the second method drawn beside trajectory matching: a
    straight line through the cell's first `window` measured SOH
    values, extended to the retirement threshold. Its implied end of
    life is a single number, so RUL at discharge index k is
    max(implied_eol - k, 0) over the same in-service discharges the
    extrapolation is scored on (start..eol-1). Columns: cell_id,
    cycle, extrapolated_rul. It uses the cell's own measured SOH,
    which a lab or full-discharge BMS has; it is not a forecast from
    the model.
    """

    cells = sorted(cells) if cells is not None else sorted(df["cell_id"].unique())
    out = []

    for t in cells:

        g = df[df["cell_id"] == t].sort_values("cycle")
        y = g["soh"].to_numpy(dtype=float)
        cycles = g["cycle"].to_numpy()

        eol = _first_eol_position(y, threshold)

        if eol is None:

            continue

        implied = _implied_eol(y, threshold, window)

        if implied is None:

            continue

        for k in range(start, eol):

            out.append({
                "cell_id": t,
                "cycle": int(cycles[k]),
                "extrapolated_rul": max(implied - k, 0.0),
            })

    return pd.DataFrame(out)


def method_disagreement_loco(
    df,
    preds,
    threshold=THRESHOLD,
    start=10,
    window=EXTRAP_WINDOW,
):
    """
    How far the two RUL methods disagree, per held-out cell.

    For every in-service discharge the two methods are scored on,
    this is |trajectory estimate - extrapolation estimate|, using the
    same point estimate and the same range as the reported MAE. The
    mean of those differences is the disagreement the report shows as
    the indicative uncertainty: it says how much the answer depends
    on which method is used, not how far either is from the truth
    (that is the reported MAE). No per-cell method is chosen; both
    are kept. Columns: test_cell, n_points, mean_abs_disagreement,
    p90_abs_disagreement.
    """

    cells = [
        c for c in sorted(df["cell_id"].unique())
        if c in preds
        and first_eol_discharge(
            df[df["cell_id"] == c]["soh"], threshold=threshold
        ) is not None
    ]

    all_cells = sorted(df["cell_id"].unique())
    out = []

    for t in cells:

        y = (
            df[df["cell_id"] == t]
            .sort_values("cycle")["soh"]
            .to_numpy(dtype=float)
        )

        eol = _first_eol_position(y, threshold)

        if eol is None:

            continue

        implied = _implied_eol(y, threshold, window)

        if implied is None:

            continue

        refs = [c for c in all_cells if c != t]
        est = TrajectoryRul.from_dataset(df, refs, threshold)
        pred = np.asarray(preds[t], dtype=float)

        diffs = []

        for k in range(start, eol):

            dist = est.remaining_distribution(pred[: k + 1])

            if dist is None:

                continue

            traj = int(round(float(np.median(dist))))
            extrap = max(implied - k, 0.0)
            diffs.append(abs(traj - extrap))

        if not diffs:

            continue

        out.append({
            "test_cell": t,
            "n_points": len(diffs),
            "mean_abs_disagreement": float(np.mean(diffs)),
            "p90_abs_disagreement": float(np.percentile(diffs, 90.0)),
        })

    return pd.DataFrame(out)


def calibrated_band_loco(
    df,
    preds,
    threshold=THRESHOLD,
    start=10,
    low_q=10.0,
    high_q=90.0,
):
    """
    A 10th/90th band calibrated from the leave-one-cell-out errors,
    and its coverage on a cell it was NOT calibrated on.

    For each held-out cell the band offsets are the low_q/high_q
    percentiles of the (truth - estimate) residuals pooled from the
    other cells, and coverage is measured on the held-out cell. This
    is the honest version of the calibration question, and with four
    cells it is expected to be unstable: only three cells calibrate
    the band, and the residuals of a cell are not independent across
    discharges, so the effective sample is closer to three than to
    the number of points. The result is reported as measured, not as
    a band that ships.
    """

    cells = [
        c for c in sorted(df["cell_id"].unique())
        if c in preds
        and first_eol_discharge(
            df[df["cell_id"] == c]["soh"], threshold=threshold
        ) is not None
    ]

    points = {}

    for t in cells:

        refs = [c for c in cells if c != t]
        points[t] = _traj_points(df, t, preds[t], refs, threshold, start)[0]

    out = []

    for t in cells:

        pool = []

        for c in cells:

            if c == t:

                continue

            pool.extend(truth - est for truth, est in points[c])

        if not pool or not points[t]:

            continue

        lo = float(np.percentile(pool, low_q))
        hi = float(np.percentile(pool, high_q))

        inside = sum(1 for truth, est in points[t] if lo <= truth - est <= hi)

        out.append({
            "test_cell": t,
            "n_points": len(points[t]),
            "n_calibration_cells": len(cells) - 1,
            "band_low_offset": lo,
            "band_high_offset": hi,
            "coverage_pct": 100.0 * inside / len(points[t]),
        })

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
    pred_arrays = {c: p["predicted_soh"].to_numpy() for c, p in preds.items()}

    res = evaluate_loco(df, pred_arrays, cells=cells)
    rounded(res).to_csv(proc / "rul_trajectory_results.csv", index=False)

    # The fade-rate extrapolation baseline, scored on exactly the
    # held-out cells the trajectory method is scored on.
    extrap = extrapolation_loco(df)
    comparison = res.merge(
        extrap[["test_cell", "extrapolation_mae"]],
        on="test_cell",
        how="left",
    )
    comparison["window"] = EXTRAP_WINDOW
    comparison = comparison[[
        "test_cell",
        "eol_discharge",
        "trajectory_mae",
        "extrapolation_mae",
        "fleet_lifetime_baseline_mae",
        "window",
    ]]
    rounded(comparison).to_csv(
        proc / "rul_method_comparison.csv", index=False
    )

    band = calibrated_band_loco(df, pred_arrays)
    rounded(band).to_csv(proc / "rul_band_calibration.csv", index=False)

    # The second method, per cycle, so the dashboard can draw both
    # side by side on every held-out cell.
    extrap_bands = extrapolation_bands(df, cells=cells)
    rounded(extrap_bands).to_csv(
        proc / "rul_extrapolation_bands.csv", index=False
    )

    # The disagreement between the two methods, the indicative
    # uncertainty the report shows instead of picking a per-cell
    # winner.
    disagreement = method_disagreement_loco(df, pred_arrays)
    rounded(disagreement).to_csv(
        proc / "rul_method_disagreement.csv", index=False
    )

    bands = prediction_bands(df, preds, cells=cells)
    rounded(bands).to_csv(proc / "rul_prediction_bands.csv", index=False)

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
        print(comparison.to_string(index=False))
        print(
            "\nMean MAE  trajectory: "
            f"{comparison['trajectory_mae'].mean():.1f}  |  "
            f"fade-rate extrapolation: "
            f"{comparison['extrapolation_mae'].mean():.1f}"
            " discharges"
        )
    else:
        print("No results produced")

    if not band.empty:
        print("\nCalibrated-band coverage on the uncalibrated cell:")
        print(band.to_string(index=False))
        print(
            f"mean coverage {band['coverage_pct'].mean():.1f}% "
            f"from {int(band['n_calibration_cells'].iloc[0])} "
            "calibration cells"
        )

    if not disagreement.empty:
        print("\nMethod disagreement (indicative uncertainty):")
        print(disagreement.to_string(index=False))
        print(
            f"mean |trajectory - extrapolation|: "
            f"{disagreement['mean_abs_disagreement'].mean():.1f} discharges"
        )

    TrajectoryRul.from_dataset(df, cells=cells).save(
        root / "models" / "rul_reference_curves.json"
    )
    print("Saved models/rul_reference_curves.json")


if __name__ == "__main__":
    main()
