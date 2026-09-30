"""
train_rul.py
-------------
Train Remaining Useful Life (RUL) prediction model.

Target
------
RUL is measured against a physical end of life, not against
the end of the test window. A cell is at end of life once it
retains 80% of its beginning-of-life capacity, so

    rul_cycles_80 = eol_cycle_threshold - cycle

Rows past that threshold are excluded, because a cell that has
already retired has no remaining useful life to predict.

What this script establishes
----------------------------
Absolute RUL is not predictable from cycle-level features on
this dataset, and the script demonstrates that rather than
asserting it. Every model is scored against three null
baselines:

  constant_train_mean  predict the training cells' mean RUL
  constant_global_mean predict the full dataset's mean RUL
  oracle_cell_mean     predict the test cell's own mean RUL

The oracle is the ceiling for any cell-agnostic model, because
it is handed the one quantity the model can never observe: the
held-out cell's actual lifetime. RUL is dominated by that
future trajectory, and four cells supply only three training
examples of how a cell degrades.

What is reported as genuinely useful
------------------------------------
The degradation rate in %SOH per cycle, per cell. This is the
physical quantity that drives RUL, it transfers across cells
far better than absolute RUL, and it is directly interpretable
by a battery management system.
"""

from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from sklearn.metrics import (
    mean_absolute_error
)

from features import (
    make_xy
)

from evaluation import (
    build_model,
    loco_evaluate,
    split_by_cell
)

from preprocessing import (
    EARLY_SLOPE_WINDOW,
    EOL_SOH_THRESHOLD
)


DATA_FILE = Path(
    "data/processed/nasa_ml_dataset.csv"
)

MODEL_DIR = Path("models")

RESULT_DIR = Path(
    "data/processed"
)

TARGET = "rul_cycles_80"

# Columns that must never reach the estimator. The end-of-life
# cycle is what RUL is defined against, so passing it in would
# hand the model the answer.
LEAKY_COLUMNS = [
    "eol_cycle_threshold",
    "is_pre_eol",
    "rul_cycles_80",
    "rul_cycles",
    "eol_cycle_observed",
    "soh_slope_pct_per_cycle",
    "soh_slope_early_pct_per_cycle"
]

# Cycle index is available in the field but encodes most of
# the target's structure, so it is ablated rather than assumed.
FEATURE_SETS = {
    "with cycle": None,
    "without cycle": "cycle"
}


def select_features(features, drop):
    """
    Return the feature list with an optional column removed.
    """

    if drop is None:

        return list(features)

    return [
        feature for feature in features
        if feature != drop
    ]


def rul_frame(df):
    """
    Return the rows that have a defined, non-negative RUL.
    """

    pre_eol = df[
        df[TARGET].notna()
        & (df[TARGET] >= 0)
    ].copy()

    return pre_eol


def evaluate(
    df,
    model_name,
    drop
):
    """
    Leave-one-cell-out evaluation for one model and
    feature set, plus the derived end-of-life column.
    """

    _, _, features = make_xy(
        df,
        TARGET
    )

    features = select_features(
        features,
        drop
    )

    rows, predictions, baselines = loco_evaluate(
        df,
        TARGET,
        features,
        model_name,
        "predicted_rul",
        leaky_columns=LEAKY_COLUMNS
    )

    for record in rows:

        record["drop"] = drop or "(none)"

    for frame in predictions:

        frame["implied_eol_cycle"] = (
            frame["cycle"] + frame["predicted_rul"]
        )

    return rows, predictions, baselines


def degradation_report(df):
    """
    Per-cell fade rate, and the remaining life it implies.
    """

    rows = []

    for cell_id, group in df.groupby("cell_id"):

        full = group["soh_slope_pct_per_cycle"].iloc[0]
        early = group["soh_slope_early_pct_per_cycle"].iloc[0]

        last = group.sort_values("cycle").iloc[-1]

        rows.append({
            "cell_id": cell_id,
            "eol_cycle": last["eol_cycle_threshold"],
            "observed_final_soh": last["soh"],
            "fade_pct_per_cycle": full,
            "early_fade_pct_per_cycle": early,
            "early_vs_full_ratio": (
                early / full
                if full else np.nan
            )
        })

    return pd.DataFrame(rows)


def extrapolated_rul(
    df,
    window=EARLY_SLOPE_WINDOW
):
    """
    Physics-based estimator: fit SOH against cycle over the
    earliest cycles, extrapolate to the threshold, and report
    the remaining life.

    This is what a battery management system could actually
    compute online, since it needs only the cycles seen so far.
    """

    rows = []

    for cell_id, group in df.groupby("cell_id"):

        group = group.sort_values("cycle")

        # Positional, not a filter on the cycle index. The
        # cycle column counts every raw sequence entry
        # (charge, discharge and impedance), so filtering on
        # it would select roughly a third of the window.
        fit = group.head(
            window
        )

        evaluate_rows = group.iloc[window:]

        if len(fit) < 5 or len(evaluate_rows) == 0:

            continue

        slope, intercept = np.polyfit(
            fit["cycle"],
            fit["soh"],
            1
        )

        if slope == 0:

            continue

        # Extrapolating the fitted line down to the threshold
        # gives the cycle at which this cell is predicted to
        # retire. A correct model makes this a single number
        # independent of when the prediction is taken.
        implied_eol = (
            EOL_SOH_THRESHOLD - intercept
        ) / slope

        estimate = np.maximum(
            0.0,
            implied_eol - evaluate_rows["cycle"]
        )

        rows.append({
            "cell_id": cell_id,
            "n_evaluated": len(evaluate_rows),
            "fitted_slope": slope,
            "implied_eol": implied_eol,
            "actual_eol": (
                evaluate_rows[
                    "eol_cycle_threshold"
                ].iloc[0]
            ),
            "MAE": mean_absolute_error(
                evaluate_rows[TARGET],
                estimate
            ),
            "constant_MAE": mean_absolute_error(
                evaluate_rows[TARGET],
                np.full(
                    len(evaluate_rows),
                    evaluate_rows[TARGET].mean()
                )
            )
        })

    return pd.DataFrame(rows)


def main():

    if not DATA_FILE.exists():

        raise FileNotFoundError(
            f"{DATA_FILE} not found.\n"
            "Run load_data.py and preprocessing.py first."
        )

    df = rul_frame(
        pd.read_csv(DATA_FILE)
    )

    MODEL_DIR.mkdir(
        exist_ok=True,
        parents=True
    )

    # train_soh.py creates this too. Doing it here means the
    # script can write its results on a checkout where only the
    # data pipeline has been run.
    RESULT_DIR.mkdir(
        exist_ok=True,
        parents=True
    )

    print(
        "\n========== RUL DATASET =========="
    )

    print(
        f"\nTarget      : {TARGET} "
        "(cycles until 80% SOH)"
    )

    print(
        f"Rows        : {len(df)} "
        f"of {len(pd.read_csv(DATA_FILE))}"
    )

    print(
        f"Range       : {df[TARGET].min():.0f} to "
        f"{df[TARGET].max():.0f} cycles"
    )

    print(
        "\nPer-cell available life:"
    )

    print(
        df.groupby("cell_id")[TARGET]
        .agg(["count", "max", "mean"])
        .round(2)
    )

    all_rows = []
    all_baselines = []
    saved = {}

    for drop in FEATURE_SETS.values():

        for model_name in [
            "Linear",
            "Ridge",
            "RandomForest",
            "XGBoost"
        ]:

            rows, predictions, baselines = evaluate(
                df,
                model_name,
                drop
            )

            all_rows.extend(rows)
            all_baselines.extend(baselines)

            if model_name == "XGBoost" and drop is None:

                saved = {
                    "predictions": predictions,
                    "model": model_name
                }

    results = pd.DataFrame(all_rows)

    baseline_frame = pd.DataFrame(
        all_baselines
    )

    print(
        "\n\n========== MODEL SCORES (leave-one-cell-out) =========="
    )

    summary = results.groupby(
        ["model", "drop"]
    )[["MAE", "RMSE", "R2"]].mean().round(3)

    print(summary.sort_values("MAE"))

    print(
        "\n\n========== NULL BASELINES (mean MAE over cells) =========="
    )

    baseline_summary = baseline_frame.groupby(
        "baseline"
    )["MAE"].mean().round(3).sort_values()

    print(baseline_summary)

    best_model_mae = (
        results
        .groupby(["model", "drop"])["MAE"]
        .mean()
        .min()
    )

    best_model_label = (
        results
        .groupby(["model", "drop"])["MAE"]
        .mean()
        .idxmin()
    )

    best_baseline_mae = baseline_summary.min()

    beats_baseline = (
        best_model_mae < best_baseline_mae
    )

    print(
        "\n\n========== VERDICT =========="
    )

    print(
        "\nBest model    : "
        f"{best_model_label[0]} "
        f"({best_model_label[1]})"
    )

    print(
        f"  MAE         : {best_model_mae:.2f} cycles"
    )

    print(
        "Best constant : "
        f"{baseline_summary.idxmin()}"
    )

    print(
        f"  MAE         : {best_baseline_mae:.2f} cycles"
    )

    if beats_baseline:

        print(
            "\nThe model beats the "
            "best constant baseline."
        )

    else:

        print(
            "\nThe model does NOT beat a constant "
            "predictor."
        )

        print(
            "RUL is dominated by the held-out "
            "cell's own future"
        )

        print(
            "trajectory, which no cycle-level "
            "feature reveals."
        )

        print(
            "Four cells provide only three "
            "training examples of"
        )

        print(
            "how a cell degrades. See the "
            "README for the full analysis."
        )

    print(
        "\n\n========== DEGRADATION RATE (usable metric) =========="
    )

    degradation = degradation_report(
        df
    )

    print(
        degradation.round(4)
        .to_string(index=False)
    )

    print(
        "\nThe early-life rate is measured over "
        f"the first {EARLY_SLOPE_WINDOW} cycles only."
    )

    print(
        "It is systematically shallower than "
        "the full-life rate"
    )

    print(
        "because SOH degradation is convex: a "
        "shallow shoulder"
    )

    print(
        "followed by an accelerating knee. A "
        "BMS extrapolating"
    )

    print(
        "RUL from early data alone must correct "
        "for this bias."
    )

    print(
        "\n\n========== ONLINE EXTRAPOLATION ESTIMATOR =========="
    )

    extrapolation = extrapolated_rul(
        df
    )

    if not extrapolation.empty:

        print(
            extrapolation.round(2)
            .to_string(index=False)
        )

        print(
            "\nMean MAE           : "
            f"{extrapolation['MAE'].mean():.2f} cycles"
        )

        print(
            "Mean constant MAE  : "
            f"{extrapolation['constant_MAE'].mean():.2f} cycles"
        )

    # ------------------------------------------------------------
    # Persist artefacts
    # ------------------------------------------------------------

    # saved is populated only by the XGBoost run with the full
    # feature set. Failing here names the cause, instead of
    # reporting a KeyError on an empty dict if that combination
    # is ever dropped from the loop above.
    if not saved:

        raise RuntimeError(
            "No RUL predictions were collected. "
            "Expected an XGBoost run with the full feature "
            "set; check FEATURE_SETS and the model list."
        )

    for prediction in saved["predictions"]:

        cell = prediction["cell_id"].iloc[0]

        prediction.to_csv(
            RESULT_DIR / f"rul_predictions_{cell}.csv",
            index=False
        )

    results.to_csv(
        RESULT_DIR / "rul_results.csv",
        index=False
    )

    baseline_frame.to_csv(
        RESULT_DIR / "rul_null_baselines.csv",
        index=False
    )

    degradation.to_csv(
        RESULT_DIR / "rul_degradation_rates.csv",
        index=False
    )

    _, _, features = make_xy(
        df,
        TARGET
    )

    features = select_features(
        features,
        None
    )

    for cell in sorted(df["cell_id"].unique()):

        train_df, _ = split_by_cell(
            df,
            cell
        )

        model = build_model(
            "XGBoost"
        )

        model.fit(
            train_df[features],
            train_df[TARGET]
        )

        joblib.dump(
            {
                "model": model,
                "features": features,
                "target": TARGET,
                "trained_on": (
                    "all cells except "
                    f"{cell}"
                ),
                "beats_constant_baseline": bool(
                    beats_baseline
                )
            },
            MODEL_DIR / f"rul_xgb_{cell}.joblib"
        )

    final_model = build_model(
        "XGBoost"
    )

    final_model.fit(
        df[features],
        df[TARGET]
    )

    joblib.dump(
        {
            "model": final_model,
            "features": features,
            "target": TARGET,
            "trained_on": "all cells",
            "beats_constant_baseline": bool(
                beats_baseline
            )
        },
        MODEL_DIR / "rul_xgb_all.joblib"
    )

    print(
        "\n\nSaved models to",
        MODEL_DIR
    )

    print(
        "Saved results to",
        RESULT_DIR
    )


if __name__ == "__main__":

    main()
