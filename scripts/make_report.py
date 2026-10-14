#!/usr/bin/env python
"""
make_report.py
--------------
Build the numbers that appear in README.md and CHANGES.md from the
measured CSVs. Nothing here is typed by hand: every value is read
from a CSV under data/processed/, formatted, and written back into
the marked regions of the documents.

A marked region looks like:

    <!-- make_report:results -->
    ... generated content ...
    <!-- make_report:results:end -->

The script replaces the content between the markers. It fails if a
required CSV is missing or empty, if a marker pair is absent, or if
a metric it needs is not present in its source CSV.

It also writes data/processed/report_summary.csv, the record of
every metric the documents report.
"""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PROC = ROOT / "data" / "processed"
EXP = PROC / "experiments"

README = ROOT / "README.md"
CHANGES = ROOT / "CHANGES.md"
SUMMARY = PROC / "report_summary.csv"

REQUIRED = {
    "clean": PROC / "nasa_ml_dataset_clean.csv",
    "audit": PROC / "cell_audit.csv",
    "soh_results": PROC / "soh_results.csv",
    "soh_nested": PROC / "soh_nested_results.csv",
    "soh_baselines": PROC / "soh_baselines.csv",
    "soh_null": PROC / "soh_null_baselines.csv",
    "rul_trajectory": PROC / "rul_trajectory_results.csv",
    "full_discharge": EXP / "soh_full_discharge.csv",
    "partial_discharge": EXP / "soh_partial_discharge.csv",
    "ablation": EXP / "ablation_features.csv",
    "logo": EXP / "logo_results.csv",
    "pack_detection": PROC / "pack_detection_summary.csv",
    "pack_false_alarms": PROC / "pack_false_alarms.csv",
}

_rows = []


def record(task, method, protocol, metric, value, unit, source):
    _rows.append({
        "task": task,
        "method": method,
        "protocol": protocol,
        "metric": metric,
        "value": value,
        "unit": unit,
        "source_file": str(Path(source).relative_to(ROOT)),
    })


def load(key):
    path = REQUIRED[key]
    if not path.exists():
        raise FileNotFoundError(f"required CSV missing: {path}")
    frame = pd.read_csv(path)
    if frame.empty:
        raise ValueError(f"required CSV is empty: {path}")
    return frame


def fmt(value, decimals=2):
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):.{decimals}f}"


def mean(series):
    return float(pd.Series(series).mean())


def replace_region(text, name, body):
    start = f"<!-- make_report:{name} -->"
    end = f"<!-- make_report:{name}:end -->"
    if start not in text or end not in text:
        raise ValueError(f"marker pair '{name}' not found")
    head, rest = text.split(start, 1)
    _, tail = rest.split(end, 1)
    return f"{head}{start}\n{body.rstrip()}\n{end}{tail}"


def model_mean(frame, model, column="MAE"):
    return float(frame.loc[frame["model"] == model, column].mean())


def build_results(clean, audit, soh_res, soh_nested, soh_base, soh_null,
                  full, partial, ablation, logo):
    lines = []

    lines.append("#### State of Health (SOH)\n")
    lines.append("| Method | Validation | Mean MAE (%) | Mean R² |")
    lines.append("|---|---|---|---|")

    coulomb = mean(soh_base.loc[soh_base["model"] == "Coulomb", "MAE"])
    coulomb_r2 = mean(soh_base.loc[soh_base["model"] == "Coulomb", "R2"])
    record("SOH", "Coulomb formula", "leave-one-cell-out (G1)",
           "mean MAE", coulomb, "%", REQUIRED["soh_baselines"])
    record("SOH", "Coulomb formula", "leave-one-cell-out (G1)",
           "mean R2", coulomb_r2, "", REQUIRED["soh_baselines"])
    lines.append(f"| Coulomb formula | leave-one-cell-out (G1) | {fmt(coulomb)} | {fmt(coulomb_r2, 3)} |")

    for model in ("ElasticNet", "Ridge", "RandomForest"):
        mae = model_mean(soh_base, model)
        r2 = mean(soh_base.loc[soh_base["model"] == model, "R2"])
        record("SOH", model, "leave-one-cell-out (G1)", "mean MAE", mae, "%",
               REQUIRED["soh_baselines"])
        record("SOH", model, "leave-one-cell-out (G1)", "mean R2", r2, "",
               REQUIRED["soh_baselines"])
        lines.append(f"| {model} (default) | leave-one-cell-out (G1) | {fmt(mae)} | {fmt(r2, 3)} |")

    xgb_fixed = mean(soh_res["MAE"])
    xgb_fixed_r2 = mean(soh_res["R2"])
    record("SOH", "XGBoost (fixed params)", "leave-one-cell-out (G1)",
           "mean MAE", xgb_fixed, "%", REQUIRED["soh_results"])
    record("SOH", "XGBoost (fixed params)", "leave-one-cell-out (G1)",
           "mean R2", xgb_fixed_r2, "", REQUIRED["soh_results"])
    lines.append(f"| XGBoost (fixed params) | leave-one-cell-out (G1) | {fmt(xgb_fixed)} | {fmt(xgb_fixed_r2, 3)} |")

    xgb_nested = mean(soh_nested["MAE"])
    xgb_nested_r2 = mean(soh_nested["R2"])
    record("SOH", "XGBoost (nested tuning)", "nested leave-one-cell-out (G1)",
           "mean MAE", xgb_nested, "%", REQUIRED["soh_nested"])
    record("SOH", "XGBoost (nested tuning)", "nested leave-one-cell-out (G1)",
           "mean R2", xgb_nested_r2, "", REQUIRED["soh_nested"])
    lines.append(f"| XGBoost (nested tuning) | nested leave-one-cell-out (G1) | {fmt(xgb_nested)} | {fmt(xgb_nested_r2, 3)} |")

    null = mean(soh_null.loc[soh_null["baseline"] == "constant_global_mean", "MAE"])
    record("SOH", "Constant global mean", "leave-one-cell-out (G1)",
           "mean MAE", null, "%", REQUIRED["soh_null"])
    lines.append(f"| Constant global mean | leave-one-cell-out (G1) | {fmt(null)} | — |")

    lines.append("\n#### Per-cell SOH error (XGBoost, nested)\n")
    lines.append("| Held-out cell | MAE (%) | RMSE (%) | R² |")
    lines.append("|---|---|---|---|")
    for _, row in soh_nested.sort_values("test_cell").iterrows():
        record("SOH", "XGBoost (nested)", f"held-out {row['test_cell']}",
               "MAE", row["MAE"], "%", REQUIRED["soh_nested"])
        record("SOH", "XGBoost (nested)", f"held-out {row['test_cell']}",
               "RMSE", row["RMSE"], "%", REQUIRED["soh_nested"])
        record("SOH", "XGBoost (nested)", f"held-out {row['test_cell']}",
               "R2", row["R2"], "", REQUIRED["soh_nested"])
        lines.append(
            f"| {row['test_cell']} | {fmt(row['MAE'])} | {fmt(row['RMSE'])} | {fmt(row['R2'], 3)} |"
        )

    lines.append("\n#### Full- versus partial-discharge SOH\n")
    lines.append("| Method | Held-out cell | MAE (%) |")
    lines.append("|---|---|---|")
    full_g1 = full[full["cell_id"].isin(["B0005", "B0006", "B0007", "B0018"])]
    for _, row in full_g1.iterrows():
        record("SOH", "full-discharge formula", f"held-out {row['cell_id']}",
               "MAE", row["mae_pct"], "%", REQUIRED["full_discharge"])
        lines.append(f"| Full-discharge formula | {row['cell_id']} | {fmt(row['mae_pct'])} |")

    full_mean = float(full_g1["mae_pct"].mean())
    record("SOH", "full-discharge formula", "mean (G1)",
           "mean MAE", full_mean, "%", REQUIRED["full_discharge"])
    lines.append(f"| Full-discharge formula | mean (G1) | {fmt(full_mean)} |")

    partial_formula = partial[partial["method"] == "partial_window_formula"]
    partial_formula = partial_formula[partial_formula["cell_id"].isin(
        ["B0005", "B0006", "B0007", "B0018"])]
    for _, row in partial_formula.iterrows():
        record("SOH", "partial-window formula", f"held-out {row['cell_id']}",
               "MAE", row["mae_pct"], "%", REQUIRED["partial_discharge"])
        lines.append(f"| Partial-window formula | {row['cell_id']} | {fmt(row['mae_pct'])} |")

    partial_g1_mean = float(partial_formula["mae_pct"].mean())
    record("SOH", "partial-window formula", "mean (G1)",
           "mean MAE", partial_g1_mean, "%", REQUIRED["partial_discharge"])
    lines.append(f"| Partial-window formula | mean (G1) | {fmt(partial_g1_mean)} |")

    lines.append("\n#### Feature-set ablation (XGBoost/ElasticNet, leave-one-cell-out on G1)\n")
    lines.append("| Feature set | Features | ElasticNet MAE (%) | XGBoost MAE (%) |")
    lines.append("|---|---|---|---|")
    labels = {
        "A_all": "A — all production features",
        "B_no_vw": "B — A without window features",
        "C_vw_temp_current": "C — window + temperature + current",
        "D_vw_only": "D — window features only",
    }
    for set_name, label in labels.items():
        rows = ablation[ablation["feature_set"] == set_name]
        en = float(rows.loc[rows["model"] == "ElasticNet", "mean_mae_pct"].iloc[0])
        xg = float(rows.loc[rows["model"] == "XGBoost", "mean_mae_pct"].iloc[0])
        n_feat = int(rows["n_features"].iloc[0])
        record("SOH", set_name, "leave-one-cell-out (G1)", "ElasticNet MAE",
               en, "%", REQUIRED["ablation"])
        record("SOH", set_name, "leave-one-cell-out (G1)", "XGBoost MAE",
               xg, "%", REQUIRED["ablation"])
        lines.append(f"| {label} | {n_feat} | {fmt(en)} | {fmt(xg)} |")

    lines.append("\n#### Cross-condition transfer (leave-one-group-out)\n")
    if "note" in logo.columns:
        lines.append("cross-condition not testable (fewer than two groups survive the audit).")
    else:
        lines.append("| Held-out group | Cells | Cycles | ElasticNet MAE (%) | XGBoost MAE (%) |")
        lines.append("|---|---|---|---|---|")
        for group in sorted(logo["test_group"].unique()):
            rows = logo[logo["test_group"] == group]
            en = float(rows.loc[rows["model"] == "ElasticNet", "mae_pct"].iloc[0])
            xg = float(rows.loc[rows["model"] == "XGBoost", "mae_pct"].iloc[0])
            cells = int(rows["n_cells"].iloc[0])
            cycles = int(rows["n_cycles"].iloc[0])
            record("SOH", "cross-condition", f"held-out {group}",
                   "ElasticNet MAE", en, "%", REQUIRED["logo"])
            record("SOH", "cross-condition", f"held-out {group}",
                   "XGBoost MAE", xg, "%", REQUIRED["logo"])
            lines.append(f"| {group} | {cells} | {cycles} | {fmt(en)} | {fmt(xg)} |")

    return "\n".join(lines)


def build_rul(rul):
    lines = []
    lines.append("| Held-out cell | EOL discharge | Trajectory MAE | Fleet-lifetime baseline MAE |")
    lines.append("|---|---|---|---|")
    for _, row in rul.sort_values("test_cell").iterrows():
        record("RUL", "trajectory", f"held-out {row['test_cell']}",
               "MAE", row["trajectory_mae"], "discharges", REQUIRED["rul_trajectory"])
        record("RUL", "fleet lifetime baseline", f"held-out {row['test_cell']}",
               "MAE", row["fleet_lifetime_baseline_mae"], "discharges",
               REQUIRED["rul_trajectory"])
        record("RUL", "EOL", f"held-out {row['test_cell']}",
               "EOL discharge", row["eol_discharge"], "discharges",
               REQUIRED["rul_trajectory"])
        lines.append(
            f"| {row['test_cell']} | {int(row['eol_discharge'])} | "
            f"{fmt(row['trajectory_mae'], 1)} | {fmt(row['fleet_lifetime_baseline_mae'], 1)} |"
        )

    traj_mean = mean(rul["trajectory_mae"])
    base_mean = mean(rul["fleet_lifetime_baseline_mae"])
    record("RUL", "trajectory", "mean (G1)", "mean MAE", traj_mean,
           "discharges", REQUIRED["rul_trajectory"])
    record("RUL", "fleet lifetime baseline", "mean (G1)", "mean MAE", base_mean,
           "discharges", REQUIRED["rul_trajectory"])
    lines.append(
        f"| **Mean** | — | {fmt(traj_mean, 1)} | {fmt(base_mean, 1)} |"
    )
    return "\n".join(lines)


def build_degradation(audit):
    kept = audit[audit["keep"]].copy()
    lines = []
    lines.append("| Cell | EOL discharge | Fade (%/discharge) | Early fade (%/discharge) |")
    lines.append("|---|---|---|---|")
    for _, row in kept.sort_values("cell_id").iterrows():
        eol = row["eol_discharge"]
        eol_txt = "—" if pd.isna(eol) else str(int(eol))
        fade = row["fade_pct_per_discharge"]
        early = row["early_fade_pct_per_discharge"]
        record("degradation", "slope", row["cell_id"], "fade",
               abs(fade) if not pd.isna(fade) else None, "%/discharge",
               REQUIRED["audit"])
        record("degradation", "slope", row["cell_id"], "early fade",
               abs(early) if not pd.isna(early) else None, "%/discharge",
               REQUIRED["audit"])
        if not pd.isna(eol):
            record("degradation", "slope", row["cell_id"], "EOL discharge",
                   int(eol), "discharges", REQUIRED["audit"])
        lines.append(
            f"| {row['cell_id']} | {eol_txt} | {fmt(abs(fade) if not pd.isna(fade) else None, 3)} "
            f"| {fmt(abs(early) if not pd.isna(early) else None, 3)} |"
        )
    return "\n".join(lines)


def build_dataset(audit):
    kept = audit[audit["keep"]].copy()
    lines = []
    lines.append("| Cell | Group | Discharges | Initial SOH (%) | Final SOH (%) | EOL discharge |")
    lines.append("|---|---|---|---|---|---|")
    for _, row in kept.sort_values("cell_id").iterrows():
        eol = row["eol_discharge"]
        eol_txt = "—" if pd.isna(eol) else str(int(eol))
        record("dataset", "summary", row["cell_id"], "discharges",
               int(row["discharges"]), "rows", REQUIRED["audit"])
        record("dataset", "summary", row["cell_id"], "initial SOH",
               row["initial_soh"], "%", REQUIRED["audit"])
        record("dataset", "summary", row["cell_id"], "final SOH",
               row["final_soh"], "%", REQUIRED["audit"])
        if not pd.isna(eol):
            record("dataset", "summary", row["cell_id"], "EOL discharge",
                   int(eol), "discharges", REQUIRED["audit"])
        lines.append(
            f"| {row['cell_id']} | {row['group']} | {int(row['discharges'])} | "
            f"{fmt(row['initial_soh'])} | {fmt(row['final_soh'])} | {eol_txt} |"
        )

    total = int(kept["discharges"].sum())
    record("dataset", "summary", "kept cells", "discharges", total, "rows",
           REQUIRED["audit"])
    lines.append(f"| **Kept total** | {kept['group'].nunique()} groups | {total} | — | — | — |")
    return "\n".join(lines)


def build_pack(detection, false_alarms):
    lines = []
    lines.append("| Fault | Injected | Rule detection | Isolation Forest | Rule median delay |")
    lines.append("|---|---|---|---|---|")
    for _, row in detection.sort_values("fault").iterrows():
        rule = float(row["rule_rate"]) * 100.0
        forest = float(row["iforest_rate"]) * 100.0
        record("pack", row["fault"], "synthetic injection", "rule detection",
               rule, "%", REQUIRED["pack_detection"])
        record("pack", row["fault"], "synthetic injection", "iforest detection",
               forest, "%", REQUIRED["pack_detection"])
        record("pack", row["fault"], "synthetic injection", "injected",
               row["n"], "cases", REQUIRED["pack_detection"])
        lines.append(
            f"| {row['fault']} | {int(row['n'])} | {fmt(rule, 1)}% | "
            f"{fmt(forest, 1)}% | {fmt(row['rule_median_delay'], 1)} |"
        )

    total = int(false_alarms["discharges"].sum())
    false_r = int(false_alarms["false_resistance_spike"].sum())
    false_t = int(false_alarms["false_thermal"].sum())
    record("pack", "clean packs", "no injected fault", "discharges",
           total, "discharges", REQUIRED["pack_false_alarms"])
    record("pack", "clean packs", "no injected fault", "false resistance spikes",
           false_r, "alarms", REQUIRED["pack_false_alarms"])
    record("pack", "clean packs", "no injected fault", "false thermal alarms",
           false_t, "alarms", REQUIRED["pack_false_alarms"])
    lines.append("")
    lines.append(
        f"Clean-pack false alarms at the resistance-spike and thermal "
        f"thresholds: {false_r} and {false_t} over {total} discharges."
    )
    return "\n".join(lines)


def build_latency(latency):
    lines = []
    lines.append("| Stage | Time (ms) |")
    lines.append("|---|---|")
    for _, row in latency.iterrows():
        record("latency", row["stage"], "local benchmark", "time",
               row["milliseconds"], "ms", REQUIRED["latency"])
        lines.append(f"| `{row['stage']}` | {fmt(row['milliseconds'], 2)} |")
    return "\n".join(lines)


def build_final(soh_res, soh_nested, soh_base, soh_null, rul):
    lines = []
    lines.append("| Task | Method | Protocol | Score | Baseline | Unit |")
    lines.append("|---|---|---|---|---|---|")

    coulomb = mean(soh_base.loc[soh_base["model"] == "Coulomb", "MAE"])
    null = mean(soh_null.loc[soh_null["baseline"] == "constant_global_mean", "MAE"])
    nested = mean(soh_nested["MAE"])
    lines.append(
        f"| SOH | Coulomb formula | leave-one-cell-out (G1) | {fmt(coulomb)} "
        f"| constant mean {fmt(null)} | MAE % |"
    )
    lines.append(
        f"| SOH | XGBoost (nested) | nested leave-one-cell-out (G1) | {fmt(nested)} "
        f"| constant mean {fmt(null)} | MAE % |"
    )

    traj_mean = mean(rul["trajectory_mae"])
    base_mean = mean(rul["fleet_lifetime_baseline_mae"])
    lines.append(
        f"| RUL | Trajectory model | leave-one-cell-out (G1) | {fmt(traj_mean, 1)} "
        f"| fleet lifetime {fmt(base_mean, 1)} | discharges |"
    )
    return "\n".join(lines)


def main():
    frames = {key: load(key) for key in REQUIRED}

    audit = frames["audit"]

    results = build_results(
        frames["clean"], audit,
        frames["soh_results"], frames["soh_nested"], frames["soh_baselines"],
        frames["soh_null"], frames["full_discharge"],
        frames["partial_discharge"], frames["ablation"], frames["logo"],
    )
    rul = build_rul(frames["rul_trajectory"])
    degradation = build_degradation(audit)
    dataset = build_dataset(audit)
    pack = build_pack(frames["pack_detection"], frames["pack_false_alarms"])
    final = build_final(
        frames["soh_results"], frames["soh_nested"], frames["soh_baselines"],
        frames["soh_null"], frames["rul_trajectory"],
    )

    readme = README.read_text()
    readme = replace_region(readme, "results", results)
    readme = replace_region(readme, "rul", rul)
    readme = replace_region(readme, "degradation", degradation)
    readme = replace_region(readme, "dataset", dataset)
    readme = replace_region(readme, "pack", pack)
    README.write_text(readme)

    changes = CHANGES.read_text()
    changes = replace_region(changes, "final", final)
    CHANGES.write_text(changes)

    pd.DataFrame(_rows).to_csv(SUMMARY, index=False)
    print(f"wrote {SUMMARY.relative_to(ROOT)} and updated README.md, CHANGES.md")


if __name__ == "__main__":
    main()
