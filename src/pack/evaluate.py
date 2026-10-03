"""
evaluate.py
-----------
Train the pack Isolation Forest and measure the alert engine.

Packs: every 3-cell combination of the four NASA cells, SOH from
the leave-one-cell-out model (the leakage-free Mode B output).

False alarms: run each healthy pack and count the discharges on
which any rule or ML flag at warn level or above fires, counted
only while all three cells are still alive.

Detection: inject each synthetic fault (resistance_spike,
cell_sag, thermal) into each cell of each pack at several start
points; record whether a matching alert fired and how many
discharges after the start.

The Isolation Forest is fitted leave-one-pack-out: trained on the
healthy features of the other packs and scored on this one, so no
pack is both training data and test data.

These are SYNTHETIC faults on real healthy NASA behaviour. The
numbers say the engine reacts to the disturbances it is designed
for; they do not estimate accuracy on real hardware faults.
"""

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pack.monitor import (  # noqa: E402
    PackMonitor,
    fit_iforest,
    save_iforest,
)
from pack.simulate import FAULTS, inject, pack_frames, replay  # noqa: E402
from rul_trajectory import TrajectoryRul  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
PROC = ROOT / "data" / "processed"

# alert codes that count as detecting each fault
EXPECT = {
    "resistance_spike": {"RESISTANCE_SPIKE"},
    "cell_sag": {"IMBALANCE", "WEAK_CELL"},
    "thermal": {"THERMAL", "THERMAL_RATE"},
}

STARTS = (30, 50)


def load_df():

    df = pd.read_csv(PROC / "nasa_ml_dataset.csv")

    pred = []

    for c in sorted(df["cell_id"].unique()):

        p = pd.read_csv(PROC / f"soh_predictions_{c}.csv")
        pred.append(p[["cell_id", "cycle", "predicted_soh"]])

    df = df.merge(
        pd.concat(pred), on=["cell_id", "cycle"], how="left"
    )

    return df


def collect_rows(frames, rul=None):

    m = PackMonitor(list(frames))
    rows = []

    for _, _ in replay(frames, m):

        rows.extend(m.ml_features().values())

    return rows


def run(frames, iforest=None, rul=None):

    m = PackMonitor(list(frames), iforest=iforest, rul=rul)

    return list(replay(frames, m))


def main():

    df = load_df()
    cells = sorted(df["cell_id"].unique())
    packs = list(itertools.combinations(cells, 3))
    base = {
        p: pack_frames(df, p, soh_col="predicted_soh")
        for p in packs
    }

    healthy_rows = {p: collect_rows(base[p]) for p in packs}

    out_rows = []
    fa = []

    for pack in packs:

        train = np.vstack(
            [healthy_rows[q] for q in packs if q != pack]
        )
        model = fit_iforest(train)
        rul = TrajectoryRul.from_dataset(
            df, [c for c in cells if c in pack]
        )

        # false alarms on the healthy pack
        reports = run(base[pack], model, rul)
        horizon = len(reports)
        warn = sum(
            any(
                a["severity"] in ("warn", "critical")
                and a["code"] not in ("RETIRE", "RETIRE_SOON")
                for a in r["alerts"]
            )
            for _, r in reports
        )
        ml = sum(
            any(a["code"] == "ANOMALY_ML" for a in r["alerts"])
            for _, r in reports
        )
        per_code = {
            f"false_{code.lower()}": sum(
                any(a["code"] == code for a in r["alerts"])
                for _, r in reports
            )
            for code in (
                "RESISTANCE_SPIKE",
                "THERMAL",
                "IMBALANCE",
                "WEAK_CELL",
                "SOH_MISMATCH",
            )
        }
        fa.append(
            {
                "pack": "+".join(pack),
                "discharges": horizon,
                "warn_or_critical_discharges": warn,
                "isolation_forest_flags": ml,
                **per_code,
            }
        )

        for kind in FAULTS:

            for cell in pack:

                for start in STARTS:

                    if start >= horizon - 5:

                        continue

                    faulted = inject(base[pack], cell, kind, start)
                    reports = run(faulted, model, rul)

                    first_rule = first_ml = None

                    for k, r in reports:

                        if k < start:

                            continue

                        hit = [
                            a
                            for a in r["alerts"]
                            if a["code"] in EXPECT[kind]
                            and a["cell"] in (cell, None)
                        ]
                        ml_hit = any(
                            a["code"] == "ANOMALY_ML"
                            and a["cell"] == cell
                            for a in r["alerts"]
                        )

                        if hit and first_rule is None:

                            first_rule = k - start

                        if ml_hit and first_ml is None:

                            first_ml = k - start

                    out_rows.append(
                        {
                            "pack": "+".join(pack),
                            "fault": kind,
                            "cell": cell,
                            "start": start,
                            "rule_detected": first_rule is not None,
                            "rule_delay_discharges": first_rule,
                            "iforest_detected": first_ml is not None,
                            "iforest_delay_discharges": first_ml,
                        }
                    )

    det = pd.DataFrame(out_rows)
    fa = pd.DataFrame(fa)

    det.to_csv(PROC / "pack_fault_detection.csv", index=False)
    fa.to_csv(PROC / "pack_false_alarms.csv", index=False)

    print(fa.to_string(index=False))

    summary = det.groupby("fault").agg(
        n=("rule_detected", "size"),
        rule_rate=("rule_detected", "mean"),
        rule_median_delay=("rule_delay_discharges", "median"),
        iforest_rate=("iforest_detected", "mean"),
    )
    print("\n", summary.round(2).to_string())

    summary.to_csv(PROC / "pack_detection_summary.csv")

    # final forest for the live demo: trained on all healthy packs
    final = fit_iforest(np.vstack(list(healthy_rows.values())))
    save_iforest(final, ROOT / "models" / "pack_iforest.joblib")
    print("\nSaved models/pack_iforest.joblib")


if __name__ == "__main__":
    main()
