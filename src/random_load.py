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
        }

    reference = reference_capacity(g_clean["capacity_ah"], REFERENCE_RULE)

    if not np.isfinite(reference) or reference <= 0:

        reference = float(g_clean["capacity_ah"].iloc[0])

    soh = g_clean["capacity_ah"].astype(float) / reference * 100.0

    below = (soh < EOL_SOH_THRESHOLD).to_numpy()
    reaches = bool(below.any())
    reason = exclusion_reason(soh)

    return {
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
            float(int(np.argmax(below)) + 1) if reaches else np.nan
        ),
    }


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
