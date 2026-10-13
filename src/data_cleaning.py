"""
data_cleaning.py
----------------
Clean battery discharge data according to strict rules.
"""
from pathlib import Path
import numpy as np
import pandas as pd

PROC = Path("data/processed")
IN = PROC / "nasa_ml_dataset.csv"
OUT_CLEAN = PROC / "nasa_ml_dataset_clean.csv"
OUT_AUDIT = PROC / "cell_audit.csv"


def _is_outlier_jump(capacity_series):
    s = pd.Series(capacity_series).astype(float).copy()
    s = s.reset_index(drop=True)
    diffs = s.diff().fillna(0)
    # count upward jumps > 3.0 Ah? units are Ah; capacity small
    jumps = (diffs > 3.0).sum()
    return int(jumps)


def main():
    df = pd.read_csv(IN)
    audits = []
    keep_cells = []

    for cell_id, g in df.groupby("cell_id"):
        g = g.sort_values("cycle").copy()
        valid_mask = g["capacity_ah"].notna()
        valid = g[valid_mask].copy()
        # drop invalid discharges: capacity near 0 or under 50% of cell's median (of valid)
        if len(valid) > 0:
            med_cap = valid["capacity_ah"].median()
            ref_cap_first5 = valid.head(5)["capacity_ah"].median() if len(valid) >= 1 else valid["capacity_ah"].iloc[0] if len(valid) else np.nan
        else:
            med_cap = g["capacity_ah"].median()
            ref_cap_first5 = np.nan
        # apply filters
        cap = g["capacity_ah"].astype(float)
        bad = (cap < 1e-6) | (cap < 0.5 * med_cap) if med_cap > 0 else (cap < 1e-6)
        g_clean = g.loc[~bad].copy()
        if len(g_clean) == 0:
            reason = "no valid discharges after cleaning"
            audits.append({
                "cell_id": cell_id,
                "group": g["group"].iloc[0] if len(g) else None,
                "discharges": len(g_clean),
                "first_capacity": float(g["capacity_ah"].iloc[0]) if len(g) else np.nan,
                "min_capacity": float(g["capacity_ah"].min()) if len(g) else np.nan,
                "last_capacity": float(g["capacity_ah"].iloc[-1]) if len(g) else np.nan,
                "below50_median_count": int(bad.sum()) if hasattr(bad, 'sum') else 0,
                "upward_jumps_gt3": _is_outlier_jump(g["capacity_ah"]),
                "soh_reaches_80": bool((g["soh"] < 80).any()),
                "keep": False,
                "reason": reason,
            })
            continue
        # recompute SOH based on reference median of first 5 valid (cleaned)
        ref = g_clean.head(5)["capacity_ah"].median()
        if pd.isna(ref) or ref <= 0:
            ref = g_clean["capacity_ah"].iloc[0]
        soh_clean = g_clean["capacity_ah"] / ref * 100.0
        # rules
        exclude = False
        reasons = []
        if (soh_clean > 105.0).any():
            exclude = True
            reasons.append("soh>105")
        if (g_clean["capacity_ah"].diff().fillna(0) > 0).sum() > len(g_clean) * 0.5:  # crude
            pass
        # simpler: if overall trend up? capacity rises overall
        if len(g_clean) >= 2 and g_clean["capacity_ah"].iloc[-1] > g_clean["capacity_ah"].iloc[0] + 1e-3:
            exclude = True
            reasons.append("capacity_rises_overall")
        # audit
        audits.append({
            "cell_id": cell_id,
            "group": g_clean["group"].iloc[0] if len(g_clean) else (g["group"].iloc[0] if len(g) else None),
            "discharges": len(g_clean),
            "first_capacity": float(g_clean["capacity_ah"].iloc[0]),
            "min_capacity": float(g_clean["capacity_ah"].min()),
            "last_capacity": float(g_clean["capacity_ah"].iloc[-1]),
            "below50_median_count": int(bad.sum()) if hasattr(bad, 'sum') else 0,
            "upward_jumps_gt3": _is_outlier_jump(g["capacity_ah"]),
            "soh_reaches_80": bool((soh_clean < 80).any()),
            "keep": not exclude,
            "reason": ";".join(reasons) if reasons else ("ok" if not exclude else "exclude"),
        })
        if not exclude:
            keep_cells.append(cell_id)

    audit_df = pd.DataFrame(audits)
    PROC.mkdir(parents=True, exist_ok=True)
    audit_df.to_csv(OUT_AUDIT, index=False)
    df_keep = df[df["cell_id"].isin(keep_cells)].copy()
    df_keep.to_csv(OUT_CLEAN, index=False)
    print(f"kept {len(keep_cells)} cells: {keep_cells}")
    print(f"wrote {OUT_AUDIT} and {OUT_CLEAN}")


if __name__ == "__main__":
    main()
