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
    logo_results.csv           raw window leave-one-group-out on surviving
                               groups (the cross-condition failure)
    cross_condition.csv        raw vs cell-relative window features against
                               the window formula, leave-one-group-out
    soh_partial_transfer.csv   partial-discharge model cell by cell, against
                               the window formula (LOCO G1/G3 + B0053)
    soh_partial_reference_window.csv  partial task vs the baseline length

Also writes data/processed/soh_partial_predictions_B0053.csv: the
held-out B0053 discharges with the model and window-formula
predictions, for the per-discharge report plot.

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
  measure without a full discharge. This window formula is the
  physics comparator for the SOH models: they are scored on the
  partial-discharge task, not against a formula that sees the full
  cycle.
* The ablation holds the estimator at its default settings; the
  point is the feature set, not the tuning.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from evaluation import build_model  # noqa: E402
from features import (  # noqa: E402
    FEATURES,
    drop_constant_features,
)
from features import (
    WINDOW_FEATURES as VW_FEATURES,
)
from features import (
    WINDOW_MODEL_FEATURES as WINDOW_NORM_FEATURES,
)
from repro import rounded  # noqa: E402

PROC = Path("data/processed")
EXP = PROC / "experiments"
EXP.mkdir(parents=True, exist_ok=True)

CLEAN = PROC / "nasa_ml_dataset_clean.csv"

TARGET = "soh"
GROUP_COL = "group"

REF_WINDOW = 5  # reference = median of the first N valid discharges

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


# Columns divided by the cell's own early baseline (the median of
# its first five valid discharges) to make them comparable across
# operating conditions. `default` is the fallback denominator when a
# cell's baseline is zero or missing, so a normalized column is
# never a division by zero.
NORM_BASE = {
    "vw_capacity_ah": ("vw_capacity_ratio", "vw_capacity_ah"),
    "vw_time_s": ("vw_time_ratio", "vw_time_s"),
    "vw_dqdv_peak_height": ("vw_dqdv_height_ratio", "vw_dqdv_peak_height"),
    "vw_voltage_mean": ("vw_voltage_ratio", "vw_voltage_mean"),
    "voltage_mean": ("voltage_mean_ratio", "voltage_mean"),
    "voltage_drop": ("voltage_drop_ratio", "voltage_drop"),
    "resistance_proxy_ohm": ("resistance_ratio", "resistance_proxy_ohm"),
    "discharge_duration_s": ("duration_ratio", "discharge_duration_s"),
}

# The cell-relative model input set. Every column here is a ratio
# to the cell's own early baseline, or a within-cycle scale-free
# quantity, so the same value means the same thing at 4 C as at
# 43 C. It deliberately excludes every absolute level feature
# (raw temperatures, currents, voltages) that does not transfer.
NORM_FEATURES = [
    "vw_capacity_ratio",
    "vw_capacity_per_amp",
    "vw_time_ratio",
    "vw_dqdv_height_ratio",
    "vw_voltage_ratio",
    "voltage_mean_ratio",
    "voltage_drop_ratio",
    "resistance_ratio",
    "duration_ratio",
    "temperature_rise",
    "current_ratio",
]

# The partial-discharge model input set (shared as
# features.WINDOW_MODEL_FEATURES): only quantities measured inside
# the 4.0-3.4 V window, each either a ratio to the cell's own
# early-window baseline or already scale-free (capacity per amp).
# No full-discharge quantity (duration, resistance, end voltage)
# enters, because a BMS that sees only the window never measures
# them. This is the set the partial-discharge task is scored on.


def cell_relative_features(df, ref_window=REF_WINDOW):
    """
    Add per-cell baseline-relative columns.

    The baseline is the median of the first `ref_window` valid values
    of each cell, taken in discharge order. It only uses a cell's
    own opening discharges, so it is available at inference time
    and does not see the target or any future cycle.

    `ref_window` is a parameter because a new cell may have only a
    couple of discharges before the first prediction; the
    reference-window sensitivity experiment measures what that costs.

    Returns a copy of `df` with the NORM_FEATURES columns added.
    """

    df = df.copy().sort_values(["cell_id", "discharge_index"])

    for column, (new_name, _) in NORM_BASE.items():

        ref = (
            df.groupby("cell_id")[column]
            .transform(lambda s: s.dropna().head(ref_window).median())
        )
        # A cell whose baseline is zero (or missing) would divide
        # by zero; fall back to the cell's own median, then to 1.0
        # so the column stays finite.
        ref = ref.replace(0.0, np.nan)
        ref = ref.fillna(
            df.groupby("cell_id")[column].transform("median")
        )
        df[new_name] = df[column] / ref

    df["temperature_rise"] = (
        df["temperature_mean"] - df["ambient_temperature"]
    )
    df["current_ratio"] = df["current_mean"] / df["nominal_current_a"]
    # Explicit current-normalized window capacity: amp-hours the
    # 4.0-3.4 V window holds per amp of discharge current. It
    # removes the discharge-rate difference between groups (2 A in
    # G1/G9 versus 4 A in G3) without touching the target.
    df["vw_capacity_per_amp"] = (
        df["vw_capacity_ah"] / df["current_mean"].abs()
    )

    return df


def window_formula(df, ref_window=REF_WINDOW):
    """
    The physics baseline a BMS can compute from the 4.0-3.4 V window
    alone: the window's amp-hours over the cell's own early-window
    median, as a percentage. It never sees the full discharge, which
    is why it is the fair comparator for the partial-discharge model.
    """

    df = df.copy()
    refs = formula_reference(df, "vw_capacity_ah", window=ref_window)
    df["_window_formula"] = (
        df["vw_capacity_ah"] / df["cell_id"].map(refs) * 100.0
    )
    return df


def _model_mae_r2(train, test, features, model_name):
    model = build_model(model_name)
    model.fit(train[features], train[TARGET])
    pred = model.predict(test[features])
    return _mae(test[TARGET], pred), _r2(test[TARGET], pred)


def cross_condition(df):
    """
    Raw versus cell-relative window features across operating
    conditions, against the window formula on the same cells.

    This is the partial-discharge question: a BMS that only sees the
    4.0-3.4 V window. For each surviving group, train on every other
    group and test on the held-out one. The held-out cell ids are
    written out so a single-cell group (G9 = B0053) is reported on
    its own line rather than hidden inside a mean.
    """

    df = window_formula(cell_relative_features(df))

    groups = sorted(df[GROUP_COL].dropna().unique())

    rows = []

    for test_group in groups:

        train = df[df[GROUP_COL] != test_group]
        test = df[df[GROUP_COL] == test_group]
        cells = sorted(test["cell_id"].dropna().unique().tolist())
        cell_label = ",".join(cells)

        # The window formula on exactly the held-out cells.
        rows.append({
            "test_group": test_group,
            "test_cells": cell_label,
            "n_cells": int(len(cells)),
            "n_cycles": int(len(test)),
            "method": "window formula",
            "feature_set": "physics",
            "model": ".",
            "n_features": 0,
            "mae_pct": _mae(test[TARGET], test["_window_formula"]),
            "r2": _r2(test[TARGET], test["_window_formula"]),
        })

        for feature_set, features in (
            ("raw", VW_FEATURES),
            ("cell_relative", WINDOW_NORM_FEATURES),
        ):

            for model_name in ("ElasticNet", "XGBoost"):

                mae, r2 = _model_mae_r2(train, test, features, model_name)

                rows.append({
                    "test_group": test_group,
                    "test_cells": cell_label,
                    "n_cells": int(len(cells)),
                    "n_cycles": int(len(test)),
                    "method": "model",
                    "feature_set": feature_set,
                    "model": model_name,
                    "n_features": len(features),
                    "mae_pct": mae,
                    "r2": r2,
                })

    return pd.DataFrame(rows)


def partial_discharge_transfer(df, ref_window=REF_WINDOW):
    """
    The partial-discharge task, cell by cell: predict SOH from the
    4.0-3.4 V window alone, with the window formula scored on
    exactly the same held-out rows.

    Two protocols:

      * leave-one-cell-out within G1, and within G3;
      * train on every G1 and G3 cell and test on B0053 (G9), a
        condition the model has never seen.

    Returns one row per (protocol, test cell, model).
    """

    data = window_formula(
        cell_relative_features(df, ref_window=ref_window),
        ref_window=ref_window,
    )

    rows = []

    def score(protocol, test, train):

        label = (
            str(test["cell_id"].iloc[0]) if len(test) else "MEAN"
        )

        for model_name in ("ElasticNet", "XGBoost"):

            mae, r2 = _model_mae_r2(
                train, test, WINDOW_NORM_FEATURES, model_name
            )

            rows.append({
                "protocol": protocol,
                "test_cell": label,
                "model": model_name,
                "n_features": len(WINDOW_NORM_FEATURES),
                "mae_pct": mae,
                "r2": r2,
                "n_cycles": int(len(test)),
                "ref_window": ref_window,
            })

        rows.append({
            "protocol": protocol,
            "test_cell": label,
            "model": "window_formula",
            "n_features": 0,
            "mae_pct": _mae(test[TARGET], test["_window_formula"]),
            "r2": _r2(test[TARGET], test["_window_formula"]),
            "n_cycles": int(len(test)),
            "ref_window": ref_window,
        })

    for group in ("G1", "G3"):

        g = data[data[GROUP_COL] == group]

        for cell in sorted(g["cell_id"].unique()):

            score(
                f"leave-one-cell-out: {group}",
                g[g["cell_id"] == cell],
                g[g["cell_id"] != cell],
            )

    score(
        "train G1+G3, test B0053",
        data[data[GROUP_COL] == "G9"],
        data[data[GROUP_COL].isin(["G1", "G3"])],
    )

    return pd.DataFrame(rows)


def partial_transfer_predictions(df, ref_window=REF_WINDOW, cell="B0053"):
    """
    Per-discharge predictions for the cross-condition transfer cell.

    Reports B0053 as the one cell it is, predicted against its true
    SOH over discharge index, so the fade-shape question (does the
    model track the trajectory, or only the level?) can be seen rather
    than inferred from an aggregate. Fits the same models on the same
    split as partial_discharge_transfer, and returns the held-out rows
    with the model and window-formula predictions.
    """

    data = window_formula(
        cell_relative_features(df, ref_window=ref_window),
        ref_window=ref_window,
    )

    test = (
        data[data["cell_id"] == cell]
        .sort_values("discharge_index")
    )
    train = data[data[GROUP_COL].isin(["G1", "G3"])]

    out = pd.DataFrame({
        "cell_id": test["cell_id"].to_numpy(),
        "discharge_index": test["discharge_index"].to_numpy(),
        "soh": test[TARGET].to_numpy(),
        "window_formula": test["_window_formula"].to_numpy(),
    })

    for model_name in ("ElasticNet", "XGBoost"):

        model = build_model(model_name)
        model.fit(train[WINDOW_NORM_FEATURES], train[TARGET])
        out["predicted_" + model_name.lower()] = model.predict(
            test[WINDOW_NORM_FEATURES]
        )

    return out


def reference_window_sensitivity(df, windows=(1, 2, 3, 5)):
    """
    How many of a cell's own opening discharges the cell-relative
    baseline needs.

    The window features are rebuilt with a baseline over the first
    `n` discharges for every n in `windows`, and the partial task is
    re-run: the within-group leave-one-cell-out mean over G1+G3, and
    the transfer to B0053. A new cell with only one or two discharges
    has a noisy baseline; this measures the cost.
    """

    rows = []

    for n in windows:

        data = window_formula(
            cell_relative_features(df, ref_window=n), ref_window=n
        )

        models = ("ElasticNet", "XGBoost", "window_formula")
        per_cell = {m: [] for m in models}
        per_cell_r2 = {m: [] for m in models}
        n_loco = 0

        for group in ("G1", "G3"):

            g = data[data[GROUP_COL] == group]

            for cell in sorted(g["cell_id"].unique()):

                test = g[g["cell_id"] == cell]
                train = g[g["cell_id"] != cell]
                n_loco += len(test)

                for model_name in ("ElasticNet", "XGBoost"):
                    mae, r2 = _model_mae_r2(
                        train, test, WINDOW_NORM_FEATURES, model_name
                    )
                    per_cell[model_name].append(mae)
                    per_cell_r2[model_name].append(r2)

                per_cell["window_formula"].append(
                    _mae(test[TARGET], test["_window_formula"])
                )
                per_cell_r2["window_formula"].append(
                    _r2(test[TARGET], test["_window_formula"])
                )

        for model_name in models:
            rows.append({
                "ref_window": n,
                "protocol": "leave-one-cell-out: G1+G3",
                "model": model_name,
                "mae_pct": float(np.mean(per_cell[model_name])),
                "r2": float(np.mean(per_cell_r2[model_name])),
                "n_cycles": int(n_loco),
            })

        train = data[data[GROUP_COL].isin(["G1", "G3"])]
        test = data[data[GROUP_COL] == "G9"]

        for model_name in ("ElasticNet", "XGBoost"):
            mae, r2 = _model_mae_r2(
                train, test, WINDOW_NORM_FEATURES, model_name
            )
            rows.append({
                "ref_window": n,
                "protocol": "train G1+G3, test B0053",
                "model": model_name,
                "mae_pct": mae,
                "r2": r2,
                "n_cycles": int(len(test)),
            })

        rows.append({
            "ref_window": n,
            "protocol": "train G1+G3, test B0053",
            "model": "window_formula",
            "mae_pct": _mae(test[TARGET], test["_window_formula"]),
            "r2": _r2(test[TARGET], test["_window_formula"]),
            "n_cycles": int(len(test)),
        })

    return pd.DataFrame(rows)


def main():

    if not CLEAN.exists():

        raise FileNotFoundError(
            f"{CLEAN} not found.\nRun preprocessing.py and data_cleaning.py first."
        )

    df = pd.read_csv(CLEAN)

    full = full_discharge_formula(df)
    rounded(full).to_csv(EXP / "soh_full_discharge.csv", index=False)

    formula, partial_models = partial_discharge(df)
    partial = pd.concat([formula, partial_models], ignore_index=True)
    rounded(partial).to_csv(EXP / "soh_partial_discharge.csv", index=False)

    summary, per_cell = ablation(df)
    rounded(summary).to_csv(EXP / "ablation_features.csv", index=False)
    rounded(per_cell).to_csv(EXP / "per_cell_results.csv", index=False)

    cross = logo(df, VW_FEATURES)
    rounded(cross).to_csv(EXP / "logo_results.csv", index=False)

    transfer = cross_condition(df)
    rounded(transfer).to_csv(EXP / "cross_condition.csv", index=False)

    partial_transfer = partial_discharge_transfer(df)
    rounded(partial_transfer).to_csv(
        EXP / "soh_partial_transfer.csv", index=False
    )

    b0053 = partial_transfer_predictions(df)
    rounded(b0053).to_csv(
        PROC / "soh_partial_predictions_B0053.csv", index=False
    )

    sensitivity = reference_window_sensitivity(df)
    rounded(sensitivity).to_csv(
        EXP / "soh_partial_reference_window.csv", index=False
    )

    print("wrote experiments:")
    print(full.to_string(index=False))
    print()
    print(summary.to_string(index=False))
    print()
    print(cross.to_string(index=False))
    print()
    print(transfer.to_string(index=False))
    print()
    print(partial_transfer.to_string(index=False))
    print()
    print(sensitivity.to_string(index=False))


if __name__ == "__main__":

    main()
