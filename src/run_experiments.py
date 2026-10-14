"""
run_experiments.py
------------------
Produce the experiment CSVs the report is built from. Every number
here is computed from data/processed/nasa_ml_dataset_clean.csv; a
blank file is never written.

Outputs (data/processed/experiments/)
    soh_full_discharge.csv     full-discharge formula vs the target
    soh_partial_discharge.csv  window-only models + window formula
    ablation_features.csv      feature-set ablation, G1 leave-one-cell-out
    per_cell_results.csv       per-cell MAE behind the ablation
    logo_results.csv           leave-one-group-out on surviving groups

Design notes
------------
* The full-discharge formula is a physics baseline, not a model:
      q = discharge_duration_s * |current_mean|
      soh_formula = q / q_ref * 100
  where q_ref is the median q of the cell's first five VALID
  discharges (the same reference step 2 uses for capacity). It is
  a formula compared against the target, never capacity against
  capacity.
* The partial-discharge formula uses the 4.0-3.4 V window instead:
      vw_soh = vw_capacity_ah / vw_capacity_ref * 100
  Window-only models use the vw_* features alone, which a BMS can
  measure without a full discharge.
* The ablation holds the estimator at its default settings; the
  point is the feature set, not the tuning.
"""

from pathlib import Path

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from evaluation import build_model  # noqa: E402
from features import FEATURES, drop_constant_features  # noqa: E402

PROC = Path("data/processed")
EXP = PROC / "experiments"
EXP.mkdir(parents=True, exist_ok=True)

CLEAN = PROC / "nasa_ml_dataset_clean.csv"

TARGET = "soh"
GROUP_COL = "group"

REF_WINDOW = 5  # reference = median of the first N valid discharges

VW_FEATURES = [
    "vw_time_s",
    "vw_capacity_ah",
    "vw_voltage_mean",
    "vw_dqdv_peak_v",
    "vw_dqdv_peak_height",
]

TEMP_CURRENT_FEATURES = [
    "temperature_mean",
    "temperature_min",
    "temperature_max",
    "temperature_std",
    "current_mean",
    "current_min",
    "current_max",
    "current_std",
]


def _mae(actual, predicted):
    return float(np.mean(np.abs(np.asarray(actual, float) - np.asarray(predicted, float))))


def _r2(actual, predicted):
    actual = np.asarray(actual, float)
    predicted = np.asarray(predicted, float)
    ss_res = float(np.sum((actual - predicted) ** 2))
    ss_tot = float(np.sum((actual - actual.mean()) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan


def production_features(df):
    """The model input set: features.FEATURES, minus any constant."""

    available = [f for f in FEATURES if f in df.columns]
    usable, _ = drop_constant_features(df[available])
    return usable


def formula_reference(df, value_col, window=REF_WINDOW):
    """Per-cell reference = median of the first `window` valid values."""

    refs = {}

    for cell_id, group in df.groupby("cell_id"):

        group = group.sort_values("discharge_index")
        values = group[value_col].dropna()

        if len(values) == 0:
            refs[cell_id] = np.nan
            continue

        refs[cell_id] = float(values.head(window).median())

    return refs


def run_formula(df, value_col, threshold_pct=0.0):
    """Score a formula column against the target, per cell and mean."""

    rows = []

    for cell_id, group in df.groupby("cell_id"):

        actual = group[TARGET].to_numpy()
        predicted = group["_formula"].to_numpy()

        rows.append({
            "cell_id": cell_id,
            "mae_pct": _mae(actual, predicted),
            "r2": _r2(actual, predicted),
            "n": int(len(group)),
        })

    frame = pd.DataFrame(rows)
    frame.loc[len(frame)] = {
        "cell_id": "MEAN",
        "mae_pct": frame["mae_pct"].mean(),
        "r2": frame["r2"].mean(),
        "n": int(frame["n"].sum()),
    }

    return frame


def full_discharge_formula(df):
    """q = duration * |current|, scored against SOH."""

    df = df.copy()
    q = df["discharge_duration_s"].abs() * df["current_mean"].abs()

    refs = formula_reference(
        df.assign(_q=q), "_q"
    )

    df["_formula"] = q / df["cell_id"].map(refs) * 100.0

    out = run_formula(df, "duration_current")
    out.insert(1, "method", "full_discharge_formula")
    out.insert(2, "formula", "duration_s*abs(current_mean) / cell reference")

    return out


def partial_discharge(df):
    """Window formula and window-only models against SOH."""

    df = df.copy()

    vw_ref = formula_reference(df, "vw_capacity_ah")
    df["_formula"] = (
        df["vw_capacity_ah"] / df["cell_id"].map(vw_ref) * 100.0
    )

    formula = run_formula(df, "vw_capacity")
    formula.insert(1, "method", "partial_window_formula")
    formula.insert(
        2, "formula",
        "vw_capacity_ah / cell reference vw_capacity_ah",
    )
    formula["target"] = TARGET

    rows = []

    for cell_id in sorted(df["cell_id"].unique()):

        train = df[df["cell_id"] != cell_id]
        test = df[df["cell_id"] == cell_id]

        for model_name in ("ElasticNet", "XGBoost"):

            model = build_model(model_name)
            model.fit(train[VW_FEATURES], train[TARGET])
            pred = model.predict(test[VW_FEATURES])

            rows.append({
                "cell_id": cell_id,
                "method": "partial_window_model",
                "model": model_name,
                "mae_pct": _mae(test[TARGET], pred),
                "r2": _r2(test[TARGET], pred),
                "n": int(len(test)),
            })

    models = pd.DataFrame(rows)

    model_means = (
        models.groupby("model", as_index=False)
        .agg(mae_pct=("mae_pct", "mean"), r2=("r2", "mean"))
    )
    model_means["cell_id"] = "MEAN"
    model_means["method"] = "partial_window_model"
    model_means["n"] = int(len(df))

    return formula, pd.concat([models, model_means], ignore_index=True)


def _loco(df, features, model_name, target=TARGET, group_col=None):
    """Leave-one-cell-out (or per-group) MAE rows."""

    rows = []

    for test_cell in sorted(df["cell_id"].unique()):

        train = df[df["cell_id"] != test_cell]
        test = df[df["cell_id"] == test_cell]

        model = build_model(model_name)
        model.fit(train[features], train[target])
        pred = model.predict(test[features])

        rows.append({
            "cell_id": test_cell,
            "mae_pct": _mae(test[target], pred),
            "r2": _r2(test[target], pred),
            "n": int(len(test)),
        })

    return rows


def ablation(df):
    """Feature-set ablation on G1, fixed estimators, LOCO."""

    g1 = df[df[GROUP_COL] == "G1"].copy()
    prod = production_features(g1)

    feature_sets = {
        "A_all": prod,
        "B_no_vw": [f for f in prod if f not in VW_FEATURES],
        "C_vw_temp_current": VW_FEATURES + TEMP_CURRENT_FEATURES,
        "D_vw_only": list(VW_FEATURES),
    }

    summary = []
    per_cell = []

    for set_name, features in feature_sets.items():

        for model_name in ("ElasticNet", "XGBoost"):

            rows = _loco(g1, features, model_name)

            frame = pd.DataFrame(rows)

            summary.append({
                "feature_set": set_name,
                "model": model_name,
                "n_features": len(features),
                "mean_mae_pct": frame["mae_pct"].mean(),
                "mean_r2": frame["r2"].mean(),
            })

            for row in rows:
                per_cell.append({
                    "task": "ablation_G1_loco",
                    "feature_set": set_name,
                    "model": model_name,
                    "cell_id": row["cell_id"],
                    "mae_pct": row["mae_pct"],
                    "r2": row["r2"],
                })

    return pd.DataFrame(summary), pd.DataFrame(per_cell)


def logo(df, prod):
    """Leave-one-group-out on every surviving group."""

    df = df.copy()
    groups = sorted(df[GROUP_COL].dropna().unique())

    if len(groups) < 2:
        return pd.DataFrame([{
            "note": "cross-condition not testable",
            "n_groups": len(groups),
        }])

    rows = []

    for test_group in groups:

        train = df[df[GROUP_COL] != test_group]
        test = df[df[GROUP_COL] == test_group]

        for model_name in ("ElasticNet", "XGBoost"):

            model = build_model(model_name)
            model.fit(train[prod], train[TARGET])
            pred = model.predict(test[prod])

            rows.append({
                "test_group": test_group,
                "model": model_name,
                "n_cells": int(test["cell_id"].nunique()),
                "n_cycles": int(len(test)),
                "mae_pct": _mae(test[TARGET], pred),
                "r2": _r2(test[TARGET], pred),
            })

    return pd.DataFrame(rows)


def main():

    if not CLEAN.exists():

        raise FileNotFoundError(
            f"{CLEAN} not found.\nRun preprocessing.py and data_cleaning.py first."
        )

    df = pd.read_csv(CLEAN)

    full = full_discharge_formula(df)
    full.to_csv(EXP / "soh_full_discharge.csv", index=False)

    formula, partial_models = partial_discharge(df)
    partial = pd.concat([formula, partial_models], ignore_index=True)
    partial.to_csv(EXP / "soh_partial_discharge.csv", index=False)

    summary, per_cell = ablation(df)
    summary.to_csv(EXP / "ablation_features.csv", index=False)
    per_cell.to_csv(EXP / "per_cell_results.csv", index=False)

    prod = production_features(df)
    cross = logo(df, prod)
    cross.to_csv(EXP / "logo_results.csv", index=False)

    print("wrote experiments:")
    print(full.to_string(index=False))
    print()
    print(summary.to_string(index=False))
    print()
    print(cross.to_string(index=False))


if __name__ == "__main__":

    main()
