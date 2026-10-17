"""
random_load.py
--------------
Do the NASA random-load groups supply a cell that reaches the 80%
retirement threshold? RUL needs a cell that actually retires, and
the project keeps only the four G1 constant-current cells, which is
often written as "only the four G1 cells reach 80%". This checks
that claim against the groups that are otherwise left out:

    G5  B0038 B0039 B0040   mixed 1/2/4 A loads, ambient 24 and 44 C
    G6  B0041..B0044        fixed 4 A / 1 A alternating, ambient 4 C

The check reuses the production SOH rule exactly: a discharge is
valid when its capacity is present, positive and at least half the
cell's median; SOH is measured against the median of the first five
valid discharges; a cell reaches 80% when any valid SOH is below 80;
and a cell is excluded when its SOH exceeds the 105% ceiling or its
capacity rises across the record.

Nothing is added to the model. These groups change load and
temperature across their record, so their features are not
comparable at a single operating condition, and they are excluded
from the constant-current SOH/RUL set for that reason — not because
they fail to age. This stage only answers the count, and reports it
rather than assuming it.

One cell, B0041, reaches the threshold but is excluded: its opening
discharges are a measurement failure (about a quarter of the group's
current, almost no voltage drop, ~0.056 Ah), and because those runs
are the majority of its record the 50%-of-median validity filter
cannot remove them. The check records each cell's opening-run and
later-life capacity so the report can say this with numbers, and
flags a reference as an artifact when it is under half the median of
the cell's later valid discharges.

Output: data/processed/random_load_rul_check.csv
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from data_cleaning import (  # noqa: E402
    EOL_SOH_THRESHOLD,
    REFERENCE_RULE,
    _valid_mask,
    exclusion_reason,
    first_eol_discharge,
    reference_capacity,
)
from load_data import parse_battery_file, resolve_raw_dir  # noqa: E402
from repro import rounded  # noqa: E402

ROOT = HERE.parent
OUT = ROOT / "data" / "processed" / "random_load_rul_check.csv"

RANDOM_LOAD_GROUPS = {
    "G5": ["B0038", "B0039", "B0040"],
    "G6": ["B0041", "B0042", "B0043", "B0044"],
}

# The reference reads the opening five discharges; the artifact guard
# compares it to the cell's later life. The later half of the valid
# record is used rather than "everything after the first five" because
# a corrupt opening block can itself be the majority of the record
# (B0041), which would leave the guard's own yardstick corrupted.
EARLY_WINDOW = 10
LATER_MEDIAN_FRACTION = 0.5


def _reference_diagnostics(g_clean, reference):
    """
    Opening-run and later-life capacity checks for one cell.

    Returns the opening-run capacity/current/voltage-drop summary,
    the median capacity of the later half of the valid record, and
    whether the reference is implausibly low against that later
    level — the signature of an opening measurement failure.
    """

    head = g_clean.head(EARLY_WINDOW)
    cap_clean = g_clean["capacity_ah"].astype(float).to_numpy()
    later = cap_clean[len(cap_clean) // 2:]
    later_median = float(np.median(later)) if len(later) else np.nan

    artifact = bool(
        np.isfinite(reference)
        and np.isfinite(later_median)
        and later_median > 0
        and reference < LATER_MEDIAN_FRACTION * later_median
    )

    return {
        "early_capacity_median_ah": float(head["capacity_ah"].median()),
        "early_current_mean_abs_a": float(head["current_mean"].abs().mean()),
        "early_voltage_drop_v": float(
            (head["voltage_start"] - head["voltage_end"]).mean()
        ),
        "later_median_capacity_ah": later_median,
        "reference_is_artifact": artifact,
    }


def audit_cell(raw_dir, cell_id, group):
    """One row: does this cell reach 80% under the production rule?"""

    g = (
        parse_battery_file(Path(raw_dir) / f"{cell_id}.mat")
        .sort_values("cycle")
        .reset_index(drop=True)
    )

    valid = _valid_mask(g["capacity_ah"])
    g_clean = g[valid].copy()

    if g_clean.empty:

        return {
            "cell_id": cell_id,
            "group": group,
            "discharges": 0,
            "reference_capacity_ah": np.nan,
            "soh_max": np.nan,
            "soh_min": np.nan,
            "reaches_80": False,
            "keep": False,
            "reason": "no valid discharges",
            "eol_discharge": np.nan,
            "early_capacity_median_ah": np.nan,
            "early_current_mean_abs_a": np.nan,
            "early_voltage_drop_v": np.nan,
            "later_median_capacity_ah": np.nan,
            "reference_is_artifact": False,
        }

    reference = reference_capacity(g_clean["capacity_ah"], REFERENCE_RULE)

    if not np.isfinite(reference) or reference <= 0:

        reference = float(g_clean["capacity_ah"].iloc[0])

    soh = g_clean["capacity_ah"].astype(float) / reference * 100.0

    eol_discharge = first_eol_discharge(soh, threshold=EOL_SOH_THRESHOLD)
    reaches = eol_discharge is not None
    reason = exclusion_reason(soh)

    row = {
        "cell_id": cell_id,
        "group": group,
        "discharges": int(len(g_clean)),
        "reference_capacity_ah": float(reference),
        "soh_max": float(soh.max()),
        "soh_min": float(soh.min()),
        "reaches_80": reaches,
        "keep": not bool(reason),
        "reason": reason,
        "eol_discharge": (
            float(eol_discharge) if reaches else np.nan
        ),
    }

    row.update(_reference_diagnostics(g_clean, reference))

    return row


def audit_random_load(raw_dir=None):
    """The per-cell check as a DataFrame, sorted by cell id."""

    raw_dir = Path(raw_dir) if raw_dir is not None else resolve_raw_dir()

    rows = [
        audit_cell(raw_dir, cell, group)
        for group, cells in RANDOM_LOAD_GROUPS.items()
        for cell in cells
    ]

    df = pd.DataFrame(rows).sort_values("cell_id").reset_index(drop=True)
    df["threshold"] = float(EOL_SOH_THRESHOLD)

    return df


def main():

    df = audit_random_load()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    rounded(df).to_csv(OUT, index=False)

    reaches = int(df["reaches_80"].sum())
    usable = int((df["reaches_80"] & df["keep"]).sum())
    excluded = df.loc[
        df["reaches_80"] & ~df["keep"], "cell_id"
    ].tolist()

    print(df.to_string(index=False))
    print(f"\ncells reaching 80%: {reaches} of {len(df)}")
    print(
        "reaching 80% and passing the keep filter: "
        f"{usable} of {len(df)}"
    )
    print(
        "reaching 80% but excluded by the ceiling/rise rule: "
        f"{excluded if excluded else 'none'}"
    )
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":

    main()
