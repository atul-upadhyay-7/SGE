"""
rul_cross_group.py
------------------
Remaining useful life for the NASA random-load cells, kept separate
from the four G1 cells.

Why a separate experiment
-------------------------
The RUL section of the report scores four constant-current G1 cells,
leave-one-cell-out, with reference trajectories drawn from the same
four cells. The random-load check (random_load.py) then showed that
four cells outside G1 also reach the 80% threshold: B0039 (G5) and
B0042, B0043, B0044 (G6). Those cells change load and temperature
across their record, so their features are not comparable at one
operating condition, which is why they are not folded into the SOH
or RUL model. This stage asks a narrower, answerable question: if
the same two RUL methods are given reference trajectories from one
group and scored on cells from the other, do they beat a
fleet-lifetime baseline?

Labels
------
RUL is measured against the same physical end of life as G1: the
first valid discharge whose SOH is below 80% of the cell's
first-five-median capacity, with the same validity rule
(capacity present, positive, and at least half the cell's median).
The label table is data/processed/rul_random_load_labels.csv.

Three protocols, three tables
-----------------------------
  (a) leave-one-cell-out inside G6 (B0042, B0043, B0044)
  (b) train the matcher on G1, test on each G6 cell
  (c) train the matcher on G6, test on each G1 cell

Each table reports both methods and the fleet-lifetime baseline for
every held-out cell. The two methods are:

  * reference-trajectory matching (rul_trajectory.TrajectoryRul): at
    the cell's current SOH, how many discharges did each reference
    cell have left; the median over references.
  * fade-rate extrapolation (rul_trajectory._implied_eol): a straight
    line through the cell's first EXTRAP_WINDOW measured SOH values,
    extended to the threshold.

The matcher is driven by the held-out cell's own measured SOH, which
a full-discharge BMS observes, rather than the cycle-level SOH model
used in the G1 section: that model is defined on constant-current
features and is not valid across these loads, so using it here would
mix an SOH-transfer question into an RUL question. The fade-rate
extrapolation reads only the held-out cell's own opening discharges,
so its number is the same in every protocol; only the matcher and the
baseline move with the training group.

Outputs
    data/processed/rul_random_load_labels.csv
    data/processed/rul_cross_group_g6_loco.csv
    data/processed/rul_cross_group_g1_to_g6.csv
    data/processed/rul_cross_group_g6_to_g1.csv
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from data_cleaning import (  # noqa: E402
    REFERENCE_RULE,
    _valid_mask,
    reference_capacity,
)
from load_data import parse_battery_file, resolve_raw_dir  # noqa: E402
from preprocessing import EOL_SOH_THRESHOLD  # noqa: E402
from repro import rounded  # noqa: E402
from rul_trajectory import (  # noqa: E402
    EXTRAP_WINDOW,
    TrajectoryRul,
    _implied_eol,
)

ROOT = HERE.parent
PROC = ROOT / "data" / "processed"

CLEAN = PROC / "nasa_ml_dataset_clean.csv"

LABELS_OUT = PROC / "rul_random_load_labels.csv"
G6_LOCO_OUT = PROC / "rul_cross_group_g6_loco.csv"
G1_TO_G6_OUT = PROC / "rul_cross_group_g1_to_g6.csv"
G6_TO_G1_OUT = PROC / "rul_cross_group_g6_to_g1.csv"

# The G1 cells that carry the SOH/RUL model, and the random-load
# cells that actually retire and pass the keep filter.
G1_CELLS = ["B0005", "B0006", "B0007", "B0018"]
G6_CELLS = ["B0042", "B0043", "B0044"]

# Random-load cells to label. B0039 is the only usable G5 cell, so it
# has no in-group siblings and is labelled but not scored; the three
# G6 cells are the ones the three protocols use.
LABEL_CELLS = {
    "B0039": "G5",
    "B0042": "G6",
    "B0043": "G6",
    "B0044": "G6",
}

# Scoring starts after the opening discharges, matching the G1 RUL
# section so the two are comparable; the extrapolation fits the first
# EXTRAP_WINDOW values, so the first scored discharge is that far in.
START = 10


def _series(raw_dir, cell, group):
    """One random-load cell's per-discharge SOH under the G1 rule."""

    g = (
        parse_battery_file(Path(raw_dir) / f"{cell}.mat")
        .sort_values("cycle")
        .reset_index(drop=True)
    )

    valid = _valid_mask(g["capacity_ah"])
    g = g[valid].reset_index(drop=True)

    reference = reference_capacity(g["capacity_ah"], REFERENCE_RULE)

    if not np.isfinite(reference) or reference <= 0:

        reference = float(g["capacity_ah"].iloc[0])

    soh = g["capacity_ah"].astype(float).to_numpy() / reference * 100.0

    return pd.DataFrame(
        {
            "cell_id": cell,
            "group": group,
            "cycle": g["cycle"].astype(int).to_numpy(),
            "discharge_index": np.arange(1, len(g) + 1),
            "soh": soh,
        }
    )


def _eol_index(soh):
    """First position whose SOH is below the retirement threshold."""

    below = np.asarray(soh, dtype=float) < EOL_SOH_THRESHOLD

    return int(np.argmax(below)) if below.any() else None


def label_table(series_by_cell):
    """Per-cell RUL labels: EOL discharge and its measured bounds."""

    rows = []

    for cell in sorted(series_by_cell):

        frame = series_by_cell[cell]
        soh = frame["soh"].to_numpy(dtype=float)
        eol = _eol_index(soh)

        rows.append(
            {
                "cell_id": cell,
                "group": frame["group"].iloc[0],
                "discharges": int(len(frame)),
                "eol_discharge": eol + 1 if eol is not None else np.nan,
                "soh_first": float(soh[0]),
                "soh_final": float(soh[-1]),
                "reaches_80": eol is not None,
            }
        )

    return pd.DataFrame(rows).sort_values("cell_id").reset_index(drop=True)


def rul_dataset(clean_path=CLEAN, raw_dir=None):
    """
    G1 measured SOH (from the cleaned dataset) plus the labelled
    random-load cells, in one frame the matcher can index:
    columns cell_id, group, cycle, soh.
    """

    raw_dir = Path(raw_dir) if raw_dir is not None else resolve_raw_dir()

    clean = pd.read_csv(clean_path)

    g1 = clean[clean["cell_id"].isin(G1_CELLS)][
        ["cell_id", "cycle", "soh"]
    ].copy()
    g1["group"] = "G1"
    g1["cycle"] = g1["cycle"].astype(int)

    parts = [g1[["cell_id", "group", "cycle", "soh"]]]

    for cell, group in LABEL_CELLS.items():

        if group == "G6":

            parts.append(
                _series(raw_dir, cell, group)[
                    ["cell_id", "group", "cycle", "soh"]
                ]
            )

    return pd.concat(parts, ignore_index=True)


def cross_group_evaluate(
    dataset,
    ref_cells,
    test_cells,
    protocol,
    start=START,
    window=EXTRAP_WINDOW,
    loco=False,
):
    """
    Score both RUL methods and the fleet-lifetime baseline on every
    held-out cell.

    ref_cells supplies the matcher's reference trajectories (and the
    baseline's lifetimes). With loco=True each held-out cell is
    dropped from that set, which is the leave-one-cell-out protocol.
    The baseline is the median reference lifetime minus the current
    discharge, the same fleet-lifetime baseline the G1 section uses.
    """

    rows = []

    for test_cell in test_cells:

        refs = [
            c
            for c in ref_cells
            if not (loco and c == test_cell)
        ]

        est = TrajectoryRul.from_dataset(dataset, refs, EOL_SOH_THRESHOLD)

        lifetimes = [r["eol"] for r in est.refs.values()]

        y = (
            dataset[dataset["cell_id"] == test_cell]
            .sort_values("cycle")["soh"]
            .to_numpy(dtype=float)
        )

        eol = _eol_index(y)

        if eol is None or not lifetimes:

            continue

        err_traj, err_base = [], []

        for k in range(start, eol):

            dist = est.remaining_distribution(y[: k + 1])

            if dist is None:

                continue

            point = int(round(float(np.median(dist))))
            baseline = max(float(np.median(lifetimes)) - k, 0.0)
            truth = eol - k

            err_traj.append(abs(point - truth))
            err_base.append(abs(baseline - truth))

        implied = _implied_eol(y, EOL_SOH_THRESHOLD, window)

        if implied is None or not err_traj:

            continue

        err_extrap = [
            abs(max(implied - k, 0.0) - (eol - k))
            for k in range(start, eol)
        ]

        rows.append(
            {
                "protocol": protocol,
                "test_cell": test_cell,
                "n_reference_cells": len(lifetimes),
                "eol_discharge": eol + 1,
                "n_points": len(err_traj),
                "trajectory_mae": float(np.mean(err_traj)),
                "extrapolation_mae": float(np.mean(err_extrap)),
                "fleet_lifetime_baseline_mae": float(np.mean(err_base)),
            }
        )

    return pd.DataFrame(rows).sort_values("test_cell").reset_index(drop=True)


def _verdict(frame):
    """Whether either method beats the baseline on a group."""

    traj = frame["trajectory_mae"].to_numpy(dtype=float)
    extrap = frame["extrapolation_mae"].to_numpy(dtype=float)
    base = frame["fleet_lifetime_baseline_mae"].to_numpy(dtype=float)

    return {
        "traj_beats_any": bool((traj < base).any()),
        "extrap_beats_any": bool((extrap < base).any()),
        "traj_beats_all": bool((traj < base).all()),
        "extrap_beats_all": bool((extrap < base).all()),
        "traj_mean": float(traj.mean()),
        "extrap_mean": float(extrap.mean()),
        "base_mean": float(base.mean()),
    }


def main():

    raw_dir = resolve_raw_dir()

    series = {
        cell: _series(raw_dir, cell, group)
        for cell, group in LABEL_CELLS.items()
    }

    labels = label_table(series)
    dataset = rul_dataset(CLEAN, raw_dir)

    g6_loco = cross_group_evaluate(
        dataset, G6_CELLS, G6_CELLS,
        "leave-one-cell-out: G6", loco=True,
    )
    g1_to_g6 = cross_group_evaluate(
        dataset, G1_CELLS, G6_CELLS, "train G1, test G6",
    )
    g6_to_g1 = cross_group_evaluate(
        dataset, G6_CELLS, G1_CELLS, "train G6, test G1",
    )

    labels_out = rounded(labels)
    g6_loco_out = rounded(g6_loco)
    g1_to_g6_out = rounded(g1_to_g6)
    g6_to_g1_out = rounded(g6_to_g1)

    labels_out.to_csv(LABELS_OUT, index=False)
    g6_loco_out.to_csv(G6_LOCO_OUT, index=False)
    g1_to_g6_out.to_csv(G1_TO_G6_OUT, index=False)
    g6_to_g1_out.to_csv(G6_TO_G1_OUT, index=False)

    print("=== RUL labels, random-load cells ===")
    print(labels_out.to_string(index=False))

    for title, frame in (
        ("(a) leave-one-cell-out inside G6", g6_loco),
        ("(b) train G1, test G6", g1_to_g6),
        ("(c) train G6, test G1", g6_to_g1),
    ):

        print(f"\n=== {title} ===")
        print(frame.to_string(index=False))

        v = _verdict(frame)

        if not v["traj_beats_any"] and not v["extrap_beats_any"]:

            print(
                "verdict: neither method beats the fleet-lifetime "
                "baseline on this group."
            )

        else:

            print(
                "verdict: trajectory beats the baseline on "
                f"{int((frame['trajectory_mae'] < frame['fleet_lifetime_baseline_mae']).sum())}"
                f" of {len(frame)} cells; extrapolation on "
                f"{int((frame['extrapolation_mae'] < frame['fleet_lifetime_baseline_mae']).sum())}"
                f" of {len(frame)} cells."
            )

    print(f"\nwrote {LABELS_OUT.relative_to(ROOT)}")
    print(f"wrote {G6_LOCO_OUT.relative_to(ROOT)}")
    print(f"wrote {G1_TO_G6_OUT.relative_to(ROOT)}")
    print(f"wrote {G6_TO_G1_OUT.relative_to(ROOT)}")


if __name__ == "__main__":

    main()
