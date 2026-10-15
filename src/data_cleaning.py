"""
data_cleaning.py
----------------
Produce the single cleaned ML dataset that every downstream stage
reads, plus the per-cell audit that justifies each keep/exclude.

Rules (every threshold is a named constant; no measurement is
typed by hand into a report):

1. A discharge is VALID when its capacity is present, positive,
   and at least 50% of that cell's median valid discharge
   capacity. Anything else is not a usable measurement.
2. SOH is recomputed against the median of the cell's first five
   VALID discharges (not the first raw row), so one bad opening
   cycle cannot define the whole scale.
3. A cell is EXCLUDED when the recomputed SOH exceeds the ceiling
   (105%) or when capacity rises overall across the record. The
   exclusion decision therefore uses exactly the SOH values that
   are saved in the cleaned file.
4. A kept cell that never falls below the 80% retirement
   threshold is labelled "limited ageing, no RUL". It contributes
   SOH rows but has no observed end of life, so it cannot supply
   RUL labels or reference trajectories. Nothing is capped
   silently: the label is explicit and the SOH values are saved
   as measured.

Outputs
    data/processed/nasa_ml_dataset_clean.csv
    data/processed/cell_audit.csv
"""

from pathlib import Path

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from causal import add_causal_features  # noqa: E402
from load_data import RATED_CAPACITY_AH  # noqa: E402
from repro import rounded  # noqa: E402
from preprocessing import (  # noqa: E402
    EARLY_SLOPE_WINDOW,
    EOL_SOH_THRESHOLD,
    ols_slope,
)

PROC = Path("data/processed")
IN = PROC / "nasa_ml_dataset.csv"
OUT_CLEAN = PROC / "nasa_ml_dataset_clean.csv"
OUT_AUDIT = PROC / "cell_audit.csv"

# A discharge below this fraction of the cell's median capacity is
# treated as a measurement failure, not a real low-capacity cycle.
MIN_VALID_FRACTION = 0.5

# SOH steps larger than this many percentage points are counted as
# upward jumps in the audit. The old rule counted capacity jumps in
# Ah (>3.0), which can never fire for these cells, so the column was
# always zero; the percentage-point rule is the one that does.
JUMP_THRESHOLD_PCT = 3.0

# Above this SOH the cell's record is internally inconsistent (the
# capacity scale drifts, e.g. a reference cycle measured early).
SOH_CEILING_PCT = 105.0

# A cell "rises overall" only when the last cleaned SOH exceeds the
# first by more than this many percentage points. The tolerance
# keeps sub-0.1pp measurement noise from excluding an otherwise
# healthy cell; the genuine G4/G7/G9 drifts are tens of points.
SOH_RISE_TOLERANCE_PCT = 0.1


def _valid_mask(capacity):
    """Boolean mask of usable discharges for one cell."""

    cap = pd.Series(capacity).astype(float)

    finite_pos = cap[(cap > 0) & np.isfinite(cap)]

    if len(finite_pos) == 0:

        return cap > 0

    median = finite_pos.median()

    return (
        cap.notna()
        & np.isfinite(cap)
        & (cap > 0)
        & (cap >= MIN_VALID_FRACTION * median)
    )


def _audit_row(cell_id, group, g, g_clean, soh, excluded, reason):
    """One row of cell_audit.csv, computed from the cleaned values."""

    cap_all = g["capacity_ah"].astype(float)
    cap_clean = g_clean["capacity_ah"].astype(float)

    jumps = int((soh.diff() > JUMP_THRESHOLD_PCT).sum())

    return {
        "cell_id": cell_id,
        "group": group,
        "discharges": int(len(g_clean)),
        "first_capacity": (
            float(cap_clean.iloc[0]) if len(cap_clean) else np.nan
        ),
        "min_capacity": (
            float(cap_clean.min()) if len(cap_clean) else np.nan
        ),
        "last_capacity": (
            float(cap_clean.iloc[-1]) if len(cap_clean) else np.nan
        ),
        "below50_median_count": int(
            (~_valid_mask(cap_all)).sum()
        ),
        "upward_jumps_gt3pp": jumps,
        "soh_max": float(soh.max()) if len(soh) else np.nan,
        "soh_min": float(soh.min()) if len(soh) else np.nan,
        "soh_reaches_80": bool((soh < EOL_SOH_THRESHOLD).any()),
        "keep": not excluded,
        "reason": reason,
    }


def _recompute_targets(df):
    """
    Recompute every target-derived column on the cleaned rows so
    the saved values are self-consistent with the recomputed SOH.
    """

    df = df.sort_values(
        ["cell_id", "cycle"]
    ).reset_index(drop=True)

    df["capacity_fade_pct"] = 100.0 - df["soh"]
    df["soh_rated"] = (
        df["capacity_ah"] / RATED_CAPACITY_AH * 100.0
    )
    df["capacity_change_ah"] = (
        df.groupby("cell_id")["capacity_ah"].diff()
    )
    df["soh_change_pct"] = (
        df.groupby("cell_id")["soh"].diff()
    )

    df["eol_cycle_observed"] = (
        df.groupby("cell_id")["discharge_index"].transform("max")
    )
    df["rul_cycles"] = (
        df["eol_cycle_observed"] - df["discharge_index"]
    )

    eol_discharge = (
        df[df["soh"] < EOL_SOH_THRESHOLD]
        .groupby("cell_id")["discharge_index"]
        .min()
    )
    df["eol_cycle_threshold"] = df["cell_id"].map(eol_discharge)

    has_eol = df["eol_cycle_threshold"].notna()
    df["is_pre_eol"] = (
        has_eol
        & (df["discharge_index"] <= df["eol_cycle_threshold"])
    )
    df["rul_cycles_80"] = (
        df["eol_cycle_threshold"] - df["discharge_index"]
    ).where(df["is_pre_eol"])

    full_slopes = {}
    early_slopes = {}

    for cell_id, group in df.groupby("cell_id"):

        life = (
            group[group["is_pre_eol"]]
            if group["is_pre_eol"].any()
            else group
        )

        full_slopes[cell_id] = ols_slope(
            life["discharge_index"], life["soh"]
        )

        early = life.head(EARLY_SLOPE_WINDOW)
        early_slopes[cell_id] = ols_slope(
            early["discharge_index"], early["soh"]
        )

    df["soh_slope_pct_per_cycle"] = df["cell_id"].map(full_slopes)
    df["soh_slope_early_pct_per_cycle"] = df["cell_id"].map(early_slopes)

    return df.replace([np.inf, -np.inf], np.nan)


def load_clean(path=OUT_CLEAN):
    """Read the cleaned dataset (the only ML dataset downstream)."""

    return pd.read_csv(path)


def kept_cells(path=OUT_AUDIT):
    """Return the list of cells whose audit says keep == True."""

    audit = pd.read_csv(path)
    return audit.loc[audit["keep"].astype(bool), "cell_id"].tolist()


def main():

    if not IN.exists():

        raise FileNotFoundError(
            f"{IN} not found.\nRun load_data.py and preprocessing.py first."
        )

    df = pd.read_csv(IN)

    parts = []
    audits = []

    for cell_id, g in df.groupby("cell_id", sort=True):

        g = g.sort_values("cycle").reset_index(drop=True)

        valid = _valid_mask(g["capacity_ah"])
        g_clean = g[valid].copy()

        group = (
            g_clean["group"].iloc[0]
            if "group" in g_clean.columns and len(g_clean)
            else (g["group"].iloc[0] if "group" in g.columns else None)
        )

        if len(g_clean) == 0:

            audits.append({
                "cell_id": cell_id,
                "group": group,
                "discharges": 0,
                "first_capacity": np.nan,
                "min_capacity": np.nan,
                "last_capacity": np.nan,
                "below50_median_count": int((~valid).sum()),
                "upward_jumps_gt3pp": 0,
                "soh_max": np.nan,
                "soh_min": np.nan,
                "soh_reaches_80": False,
                "keep": False,
                "reason": "no valid discharges",
            })
            continue

        reference = g_clean.head(5)["capacity_ah"].median()

        if not np.isfinite(reference) or reference <= 0:

            reference = g_clean["capacity_ah"].iloc[0]

        soh = g_clean["capacity_ah"] / reference * 100.0

        g_clean["reference_capacity_ah"] = reference
        g_clean["soh"] = soh

        reaches_80 = bool((soh < EOL_SOH_THRESHOLD).any())

        reasons = []
        excluded = False

        if soh.max() > SOH_CEILING_PCT:

            excluded = True
            reasons.append(f"soh>{int(SOH_CEILING_PCT)}")

        if (
            len(g_clean) >= 2
            and soh.iloc[-1] > soh.iloc[0] + SOH_RISE_TOLERANCE_PCT
        ):

            excluded = True
            reasons.append("capacity_rises_overall")

        if excluded:

            reason = ";".join(reasons)

        elif not reaches_80:

            reason = "limited ageing, no RUL"

        else:

            reason = "ok"

        audits.append(
            _audit_row(
                cell_id, group, g, g_clean, soh, excluded, reason
            )
        )

        if not excluded:

            parts.append(g_clean)

    if not parts:

        raise RuntimeError(
            "No cell survived cleaning; refusing to write an empty dataset."
        )

    cleaned = pd.concat(parts, ignore_index=True)

    # Rebuild causal features and discharge_index on the cleaned rows
    # only, so a dropped discharge cannot influence a later feature.
    cleaned = add_causal_features(cleaned)
    cleaned = _recompute_targets(cleaned)

    cleaned = cleaned.dropna(subset=["soh", "rul_cycles"])

    PROC.mkdir(parents=True, exist_ok=True)

    audit_df = pd.DataFrame(audits).sort_values("cell_id")

    summary = (
        cleaned.groupby("cell_id")
        .agg(
            initial_soh=("soh", "first"),
            final_soh=("soh", "last"),
            eol_discharge=("eol_cycle_threshold", "first"),
            fade_pct_per_discharge=("soh_slope_pct_per_cycle", "first"),
            early_fade_pct_per_discharge=(
                "soh_slope_early_pct_per_cycle",
                "first",
            ),
        )
        .reset_index()
    )

    audit_df = audit_df.merge(summary, on="cell_id", how="left")

    rounded(audit_df).to_csv(OUT_AUDIT, index=False)
    rounded(cleaned).to_csv(OUT_CLEAN, index=False)

    kept = audit_df.loc[audit_df["keep"], "cell_id"].tolist()
    limited = audit_df.loc[
        audit_df["reason"] == "limited ageing, no RUL", "cell_id"
    ].tolist()

    print(f"kept cells ({len(kept)}): {kept}")
    print(f"limited ageing, no RUL: {limited}")
    print(
        "excluded cells: "
        f"{audit_df.loc[~audit_df['keep'], 'cell_id'].tolist()}"
    )
    print(f"\nwrote {OUT_AUDIT} ({len(audit_df)} cells)")
    print(f"wrote {OUT_CLEAN} ({cleaned.shape[0]} rows x {cleaned.shape[1]} cols)")


if __name__ == "__main__":

    main()
