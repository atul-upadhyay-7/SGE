"""
window_partial.py
-----------------
How partial is the "partial window" really?

The partial-window SOH model scores a discharge from the 4.0-3.4 V
window alone, and the documents call that a "partial discharge". That
phrasing is easy to read as "a small early slice". This stage measures
the claim instead of asserting it: for every kept discharge it takes
the fraction of the discharge's capacity that is delivered inside the
4.0-3.4 V window (vw_capacity_ah / capacity_ah) and the fraction of
its duration that lies inside the window (vw_time_s /
discharge_duration_s), then averages per cell and overall.

The window is bounded by construction: a discharge starts above
4.0 V and (for most cells) ends below 3.4 V, so it is a middle
portion, not the whole run. Whether it is "most of the discharge" or a
small part then becomes a number that can be quoted, and the two
extremes show the truth is not uniform — the cold G9 cell traverses
the band early and holds little of its capacity there.

Input:  data/processed/nasa_ml_dataset_clean.csv (the dataset every
        downstream stage reads)
Output: data/processed/soh_window_partial.csv (per cell, plus MEAN)
"""

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from repro import rounded  # noqa: E402

ROOT = HERE.parent
CLEAN = ROOT / "data" / "processed" / "nasa_ml_dataset_clean.csv"
OUT = ROOT / "data" / "processed" / "soh_window_partial.csv"

REQUIRED_COLUMNS = [
    "cell_id",
    "group",
    "vw_capacity_ah",
    "capacity_ah",
    "vw_time_s",
    "discharge_duration_s",
]


def partial_table(clean=None):
    """
    Per-cell capacity and time share inside the trained 4.0-3.4 V
    window, with a MEAN row, as a DataFrame.
    """

    if clean is None:

        clean = pd.read_csv(CLEAN)

    if clean.empty:

        raise ValueError(f"{CLEAN.name} is empty")

    missing = [
        column for column in REQUIRED_COLUMNS
        if column not in clean.columns
    ]

    if missing:

        raise KeyError(
            f"{CLEAN.name} is missing required columns: {missing}"
        )

    invalid = (clean["capacity_ah"] <= 0) | (
        clean["discharge_duration_s"] <= 0
    )

    if invalid.any():

        raise ValueError(
            f"{CLEAN.name} has {int(invalid.sum())} discharge(s) with a "
            "non-positive capacity or duration; the fraction would be "
            "undefined"
        )

    frame = clean.copy()
    frame["capacity_inside_window_pct"] = (
        frame["vw_capacity_ah"] / frame["capacity_ah"] * 100.0
    )
    frame["time_inside_window_pct"] = (
        frame["vw_time_s"] / frame["discharge_duration_s"] * 100.0
    )

    grouped = (
        frame.groupby(["cell_id", "group"], as_index=False)
        .agg(
            n_cycles=("capacity_ah", "size"),
            capacity_inside_window_pct=(
                "capacity_inside_window_pct", "mean"
            ),
            time_inside_window_pct=("time_inside_window_pct", "mean"),
        )
        .sort_values("cell_id")
        .reset_index(drop=True)
    )

    grouped.loc[len(grouped)] = {
        "cell_id": "MEAN",
        "group": "",
        "n_cycles": int(grouped["n_cycles"].sum()),
        "capacity_inside_window_pct": float(
            grouped["capacity_inside_window_pct"].mean()
        ),
        "time_inside_window_pct": float(
            grouped["time_inside_window_pct"].mean()
        ),
    }

    return grouped


def main():

    frame = partial_table()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    rounded(frame).to_csv(OUT, index=False)

    print(frame.to_string(index=False))

    mean = frame[frame["cell_id"] == "MEAN"].iloc[0]
    per_cell = frame[frame["cell_id"] != "MEAN"]

    print(
        f"\nmean over {len(per_cell)} cells: "
        f"{mean['capacity_inside_window_pct']:.2f}% of capacity and "
        f"{mean['time_inside_window_pct']:.2f}% of the time inside the "
        "4.0-3.4 V window"
    )
    print(
        "range: "
        f"{per_cell['capacity_inside_window_pct'].min():.2f}% to "
        f"{per_cell['capacity_inside_window_pct'].max():.2f}% of capacity"
    )
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":

    main()
