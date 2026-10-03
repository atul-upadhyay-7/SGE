"""
monitor.py
----------
Pack-level health, anomaly and maintenance logic for a series
pack (3S1P / 3S3P: three cells in series, each "cell" being one
18650 or three in parallel that share a tap).

Inputs are per-cell, per-cycle readings. The SOH comes from the
trained XGBoost model through the streaming service; resistance,
end-of-discharge voltage and temperature come from telemetry.

What it reports
---------------
  per cell   SOH, resistance and its change against the cell's
             own baseline, end-of-discharge voltage, peak
             temperature, trailing fade rate, and an
             extrapolated number of cycles until the retirement
             threshold (80% capacity, from the project brief).
  pack       imbalance (voltage and SOH spread across cells),
             the weakest cell, and one maintenance state.
  alerts     rule flags and an Isolation Forest flag, each with
             the value and the threshold that fired it.

Rules, and where the numbers come from
--------------------------------------
Voltage spread between series cells, from BMS practice: a few mV
to about 30 mV is normal, 30-50 mV may trigger balancing,
50-100 mV needs attention, above 100 mV is a weak cell, an aged
cell or a connection problem (BMS vendor guidance and the TI
cell-balancing seminar). A cell that is the lowest at the end of
discharge again and again is the stronger signal, so persistence
is checked separately.

Temperature: the multi-level scheme in the thermal-runaway
literature uses an absolute warning level plus a rate-of-rise
limit (a warning above about 70 C and a rate above 1 C/s as the
industry-standard pair; Energies 2026, 19(3), 858). A bench 18650
discharge should never be near that, so the warning level here is
lower (55 C) and the critical level is the literature's 70 C.

Resistance: a spike is resistance far above the cell's own early
baseline, or far above its own trailing scatter. Both use the
cell's own history because absolute resistance differs between
cells.

Honest limits
-------------
Thresholds are engineering defaults, not tuned on faulted
hardware, which this project does not have. The Isolation
Forest has no ground-truth labels; it is an alert layer that
ranks unusual cycles, and its score is not an accuracy figure.
Cycles-to-retirement is a straight-line extrapolation of the
recent fade rate, so it is rough near a knee.
"""

from collections import deque
from pathlib import Path

import numpy as np

SEVERITY = {"ok": 0, "watch": 1, "warn": 2, "critical": 3}

THRESHOLDS = {
    "retire_soh_pct": 80.0,
    "retire_soon_cycles": 50,
    "retire_watch_cycles": 100,
    "imbalance_watch_v": 0.030,
    "imbalance_warn_v": 0.050,
    "imbalance_critical_v": 0.100,
    "soh_spread_warn_pct": 5.0,
    "soh_spread_critical_pct": 10.0,
    "weakest_persist_n": 5,
    "weakest_persist_min": 4,
    "temp_warn_c": 55.0,
    "temp_critical_c": 70.0,
    "dtdt_critical_c_per_s": 1.0,
    "temp_spread_warn_c": 8.0,
    "resistance_baseline_n": 5,
    "resistance_spike_ratio": 1.5,
    "resistance_spike_z": 4.0,
    "fade_window": 20,
    "fade_min_points": 5,
}

MAINTENANCE = {
    0: "OK",
    1: "WATCH",
    2: "SERVICE",
    3: "STOP",
}


def _robust_z(history, value):

    h = np.asarray(history, dtype=float)

    if len(h) < 5:

        return 0.0

    med = np.median(h)
    mad = np.median(np.abs(h - med)) * 1.4826

    # floor so a perfectly flat history cannot make a tiny
    # change look infinite
    mad = max(mad, 0.02 * abs(med), 1e-9)

    return float((value - med) / mad)


class CellState:

    def __init__(self, cell_id, th, rul=None):

        self.cell_id = cell_id
        self.th = th
        self.rul = rul
        self.soh = deque(maxlen=th["fade_window"])
        self.resistance = deque(maxlen=30)
        self._elev = deque(maxlen=2)
        self._soh_raw = deque(maxlen=3)
        self.r_baseline = None
        self.latest = {}

    def update(self, reading):

        th = self.th

        soh = reading.get("soh")

        if soh is not None and np.isfinite(soh):

            # Median of the last three predictions, so one bad
            # cycle (dropped samples) cannot trigger a retirement
            # or mismatch alert on its own.
            self._soh_raw.append(float(soh))
            soh = float(np.median(self._soh_raw))
            reading = dict(reading)
            reading["soh"] = soh
            self.soh.append(soh)

        r = reading.get("resistance_ohm")

        r_valid = r is not None and np.isfinite(r) and r > 0

        if r_valid:

            if (
                self.r_baseline is None
                and len(self.resistance)
                >= th["resistance_baseline_n"]
            ):

                self.r_baseline = float(
                    np.median(
                        list(self.resistance)[
                            : th["resistance_baseline_n"]
                        ]
                    )
                )

            reading = dict(reading)
            z = _robust_z(self.resistance, r)
            reading["_r_z"] = z
            self.resistance.append(float(r))

            # A step-method estimate from one discharge can be a
            # one-off outlier (missed step, noisy sample). A spike
            # is reported only when two valid readings in a row
            # are elevated.
            high = z >= th["resistance_spike_z"] or (
                self.r_baseline is not None
                and r / self.r_baseline >= th["resistance_spike_ratio"]
            )
            self._elev.append(bool(high))
            reading["_r_elev2"] = len(self._elev) == 2 and all(
                self._elev
            )

        self.latest = reading

    def fade_rate(self):
        """SOH points per cycle over the trailing window."""

        th = self.th

        if len(self.soh) < th["fade_min_points"]:

            return None

        y = np.asarray(self.soh, dtype=float)
        x = np.arange(len(y), dtype=float)

        return float(np.polyfit(x, y, 1)[0])

    def cycles_to_retirement(self):

        if not self.soh:

            return None

        soh = self.soh[-1]
        target = self.th["retire_soh_pct"]

        if soh <= target:

            return 0

        # Reference-trajectory matching when available: a straight
        # line through a convex curve overshoots by hundreds of
        # cycles before the knee (see rul_trajectory.py).
        if self.rul is not None:

            return self.rul.remaining(list(self.soh))

        slope = self.fade_rate()

        if slope is None or slope > -1e-4:

            return None

        return int(np.ceil((soh - target) / -slope))

    def resistance_ratio(self):

        r = self.latest.get("resistance_ohm")

        if (
            self.r_baseline is None
            or r is None
            or not np.isfinite(r)
        ):

            return None

        return float(r / self.r_baseline)


class PackMonitor:

    def __init__(
        self,
        cell_ids,
        thresholds=None,
        iforest=None,
        rul=None,
    ):

        self.th = dict(THRESHOLDS)

        if thresholds:

            self.th.update(thresholds)

        self.cells = {
            c: CellState(c, self.th, rul) for c in cell_ids
        }

        self.iforest = iforest
        self._weakest = deque(
            maxlen=self.th["weakest_persist_n"]
        )

    def update(self, cell_id, reading):

        self.cells[cell_id].update(reading)

    # --------------------------------------------------

    def ml_features(self):
        """
        One row per cell, relative to the pack so the model
        learns "unusual for this pack", not absolute values.
        """

        latest = {
            c: s.latest
            for c, s in self.cells.items()
            if s.latest
        }

        if len(latest) < 2:

            return {}

        def med(key):

            vals = [
                v[key]
                for v in latest.values()
                if v.get(key) is not None
                and np.isfinite(v[key])
            ]

            return float(np.median(vals)) if vals else 0.0

        v_med = med("v_end")
        s_med = med("soh")
        t_med = med("temp_max")

        out = {}

        for c, s in self.cells.items():

            if not s.latest:

                continue

            ratio = s.resistance_ratio()

            out[c] = [
                1.0 if ratio is None else ratio,
                1000.0 * (s.latest.get("v_end", v_med) - v_med),
                s.latest.get("soh", s_med) - s_med,
                s.latest.get("temp_max", t_med) - t_med,
            ]

        return out

    # --------------------------------------------------

    def evaluate(self):

        th = self.th
        alerts = []

        def flag(code, severity, cell, message, value, limit):

            alerts.append(
                {
                    "code": code,
                    "severity": severity,
                    "cell": cell,
                    "message": message,
                    "value": value,
                    "threshold": limit,
                }
            )

        live = {
            c: s for c, s in self.cells.items() if s.latest
        }

        # ---- per cell ------------------------------------

        cells_out = {}

        for c, s in live.items():

            lat = s.latest
            cycles_left = s.cycles_to_retirement()

            ratio = s.resistance_ratio()

            cells_out[c] = {
                "cycle": lat.get("cycle"),
                "soh_pct": lat.get("soh"),
                "resistance_ohm": lat.get("resistance_ohm"),
                "resistance_ratio": ratio,
                "v_end": lat.get("v_end"),
                "temp_max_c": lat.get("temp_max"),
                "fade_pct_per_cycle": s.fade_rate(),
                "cycles_to_retirement": cycles_left,
            }

            if cycles_left == 0:

                flag(
                    "RETIRE",
                    "critical",
                    c,
                    f"{c} is at or below the "
                    f"{th['retire_soh_pct']:.0f}% retirement "
                    "threshold.",
                    lat.get("soh"),
                    th["retire_soh_pct"],
                )

            elif (
                cycles_left is not None
                and cycles_left <= th["retire_soon_cycles"]
            ):

                flag(
                    "RETIRE_SOON",
                    "warn",
                    c,
                    f"{c} is about {cycles_left} cycles from "
                    "retirement at its recent fade rate.",
                    cycles_left,
                    th["retire_soon_cycles"],
                )

            elif (
                cycles_left is not None
                and cycles_left <= th["retire_watch_cycles"]
            ):

                flag(
                    "RETIRE_WATCH",
                    "watch",
                    c,
                    f"{c} is about {cycles_left} cycles from "
                    "retirement.",
                    cycles_left,
                    th["retire_watch_cycles"],
                )

            z = lat.get("_r_z", 0.0)

            if lat.get("_r_elev2"):

                flag(
                    "RESISTANCE_SPIKE",
                    "warn",
                    c,
                    f"{c} internal resistance jumped "
                    f"(x{ratio:.2f} of its baseline, "
                    f"z={z:.1f})."
                    if ratio is not None
                    else f"{c} internal resistance jumped "
                    f"(z={z:.1f}).",
                    ratio if ratio is not None else z,
                    th["resistance_spike_ratio"],
                )

            t = lat.get("temp_max")

            if t is not None and np.isfinite(t):

                if t >= th["temp_critical_c"]:

                    flag(
                        "THERMAL",
                        "critical",
                        c,
                        f"{c} reached {t:.1f} C.",
                        t,
                        th["temp_critical_c"],
                    )

                elif t >= th["temp_warn_c"]:

                    flag(
                        "THERMAL",
                        "warn",
                        c,
                        f"{c} reached {t:.1f} C.",
                        t,
                        th["temp_warn_c"],
                    )

            dtdt = lat.get("dtdt_c_per_s")

            if (
                dtdt is not None
                and np.isfinite(dtdt)
                and dtdt >= th["dtdt_critical_c_per_s"]
            ):

                flag(
                    "THERMAL_RATE",
                    "critical",
                    c,
                    f"{c} temperature is rising at "
                    f"{dtdt:.2f} C/s.",
                    dtdt,
                    th["dtdt_critical_c_per_s"],
                )

        # ---- across cells --------------------------------

        pack = {
            "voltage_spread_v": None,
            "soh_spread_pct": None,
            "temp_spread_c": None,
            "weakest_cell": None,
        }

        if len(live) >= 2:

            vends = {
                c: s.latest["v_end"]
                for c, s in live.items()
                if s.latest.get("v_end") is not None
            }

            if len(vends) >= 2:

                spread = max(vends.values()) - min(
                    vends.values()
                )
                weakest = min(vends, key=vends.get)

                pack["voltage_spread_v"] = spread
                pack["weakest_cell"] = weakest

                self._weakest.append(weakest)

                if spread >= th["imbalance_critical_v"]:

                    sev, lim = (
                        "critical",
                        th["imbalance_critical_v"],
                    )

                elif spread >= th["imbalance_warn_v"]:

                    sev, lim = "warn", th["imbalance_warn_v"]

                elif spread >= th["imbalance_watch_v"]:

                    sev, lim = "watch", th["imbalance_watch_v"]

                else:

                    sev = None

                if sev:

                    flag(
                        "IMBALANCE",
                        sev,
                        weakest,
                        f"Cell voltage spread (same instant) is "
                        f"{spread * 1000:.0f} mV; lowest is "
                        f"{weakest}.",
                        spread,
                        lim,
                    )

                if (
                    spread >= th["imbalance_watch_v"]
                    and len(self._weakest)
                    == th["weakest_persist_n"]
                    and self._weakest.count(weakest)
                    >= th["weakest_persist_min"]
                ):

                    flag(
                        "WEAK_CELL",
                        "warn",
                        weakest,
                        f"{weakest} has been the lowest cell "
                        f"in {self._weakest.count(weakest)} of "
                        f"the last {len(self._weakest)} "
                        "discharges.",
                        self._weakest.count(weakest),
                        th["weakest_persist_min"],
                    )

            sohs = [
                s.latest["soh"]
                for s in live.values()
                if s.latest.get("soh") is not None
            ]

            if len(sohs) >= 2:

                sp = max(sohs) - min(sohs)
                pack["soh_spread_pct"] = sp

                if sp >= th["soh_spread_critical_pct"]:

                    flag(
                        "SOH_MISMATCH",
                        "critical",
                        None,
                        f"Cell SOH differs by {sp:.1f} points.",
                        sp,
                        th["soh_spread_critical_pct"],
                    )

                elif sp >= th["soh_spread_warn_pct"]:

                    flag(
                        "SOH_MISMATCH",
                        "warn",
                        None,
                        f"Cell SOH differs by {sp:.1f} points.",
                        sp,
                        th["soh_spread_warn_pct"],
                    )

            temps = [
                s.latest["temp_max"]
                for s in live.values()
                if s.latest.get("temp_max") is not None
            ]

            if len(temps) >= 2:

                ts = max(temps) - min(temps)
                pack["temp_spread_c"] = ts

                if ts >= th["temp_spread_warn_c"]:

                    flag(
                        "TEMP_SPREAD",
                        "warn",
                        None,
                        f"Cell temperatures differ by "
                        f"{ts:.1f} C.",
                        ts,
                        th["temp_spread_warn_c"],
                    )

        # ---- Isolation Forest alert layer ----------------

        if self.iforest is not None:

            feats = self.ml_features()

            if feats:

                # one batched call: scoring per cell is slow. A
                # negative decision value is what predict() maps
                # to -1 (outlier).
                names = list(feats)
                scores = self.iforest.decision_function(
                    [feats[c] for c in names]
                )

                for c, score in zip(names, scores):

                    score = float(score)
                    cells_out[c]["anomaly_score"] = score

                    if score < 0:

                        flag(
                            "ANOMALY_ML",
                            "watch",
                            c,
                            f"Isolation Forest finds {c}'s cycle "
                            "unusual for this pack.",
                            score,
                            0.0,
                        )

        worst = max(
            (SEVERITY[a["severity"]] for a in alerts),
            default=0,
        )

        return {
            "maintenance": MAINTENANCE[worst],
            "cells": cells_out,
            "pack": pack,
            "alerts": alerts,
        }


def fit_iforest(rows, contamination=0.02, seed=42):
    """
    Fit the Isolation Forest on rows of PackMonitor.ml_features()
    gathered from healthy operation.
    """

    from sklearn.ensemble import IsolationForest

    model = IsolationForest(
        n_estimators=200,
        contamination=contamination,
        random_state=seed,
    )

    model.fit(np.asarray(rows, dtype=float))

    return model


def save_iforest(model, path):

    import joblib

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)


def load_iforest(path):

    import joblib

    path = Path(path)

    return joblib.load(path) if path.exists() else None
