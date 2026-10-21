"""
train_soh.py
-------------
Train State-of-Health (SOH) estimation model.

SOH is the percentage of a cell's beginning-of-life capacity
that remains, so unlike RUL it is a property of the present
state of the cell. That makes it genuinely transferable
across cells, and the leave-one-cell-out scores reflect
something real.

What this script reports
------------------------
1. A baseline ladder. Every score is meaningless without one,
   so Linear, Ridge and RandomForest are scored under the
   identical protocol to show what XGBoost is actually buying.

2. Two hyperparameter estimates. A search scored on the same
   leave-one-cell-out folds it tuned against is optimistic,
   because the held-out cell influenced the chosen parameters.
   The headline number therefore comes from a nested search,
   where parameters are tuned on the training cells only and
   the held-out cell is scored once. The optimistic figure is
   printed beside it to quantify the gap.

3. Per-cell error bias, which is where the real weakness is.
   MAE averaged over four cells hides that one cell fails
   badly and for a specific, diagnosable reason.

4. A model trained on all cells, which is the one a deployed
   system would actually use.
"""


from pathlib import Path

import os

import joblib
import pandas as pd

from sklearn.metrics import (
    mean_absolute_error
)
from sklearn.model_selection import (
    LeaveOneGroupOut,
    RandomizedSearchCV
)

from repro import rounded
from evaluation import (
    MODEL_NAMES,
    BlendRegressor,
    build_model,
    coulomb_baseline,
    loco_evaluate,
    score,
    split_by_cell
)

from features import (
    drop_constant_features,
    make_xy
)


DATA_FILE = Path(
    "data/processed/nasa_ml_dataset_clean.csv"
)

MODEL_DIR = Path("models")

RESULT_DIR = Path(
    "data/processed"
)

TARGET = "soh"

PREDICTION_COLUMN = "predicted_soh"

# Search space for XGBoost, centred on the hand-tuned
# defaults. Only the model step is addressed because the
# estimator sits inside a pipeline.
PARAM_DISTRIBUTIONS = {

    "model__n_estimators": [
        200, 400, 600, 800, 1000
    ],
    "model__max_depth": [
        2, 3, 4, 5, 6, 8
    ],
    "model__learning_rate": [
        0.01, 0.02, 0.03, 0.05, 0.1
    ],
    "model__subsample": [
        0.6, 0.7, 0.85, 1.0
    ],
    "model__colsample_bytree": [
        0.6, 0.7, 0.85, 1.0
    ],
    "model__min_child_weight": [
        1, 3, 5, 10
    ],
    "model__reg_alpha": [
        0.0, 0.1, 1.0
    ],
    "model__reg_lambda": [
        1.0, 2.0, 5.0
    ]

}

# Kept modest so the nested search stays a few minutes.
# Raise for a real tuning run.
N_ITERATIONS = int(os.environ.get("SGE_N_ITER", "20"))
if N_ITERATIONS < 1:
    raise ValueError("SGE_N_ITER must be a positive integer")

RANDOM_STATE = 42


def search_best_params(
    X,
    y,
    groups,
    n_iter=N_ITERATIONS
):
    """
    Randomised search with leave-one-cell-out grouping.
    """

    search = RandomizedSearchCV(
        estimator=build_model(
            "XGBoost"
        ),
        param_distributions=(
            PARAM_DISTRIBUTIONS
        ),
        n_iter=n_iter,
        cv=LeaveOneGroupOut(),
        scoring="neg_mean_absolute_error",
        random_state=RANDOM_STATE,
        n_jobs=-1,
        refit=True
    )

    # groups is a fit-time argument in current scikit-learn,
    # not a constructor argument.
    search.fit(X, y, groups=groups)

    return search.best_params_, search.best_score_


def nested_loco(
    df,
    features,
    n_iter=N_ITERATIONS
):
    """
    Honest hyperparameter estimate.

    For each held-out cell, parameters are searched using only
    the remaining cells, then the chosen model is scored once
    on the cell that was never seen. Nothing about the test
    cell reaches the tuning.
    """

    rows = []
    predictions = []

    for test_cell in sorted(
        df["cell_id"].unique()
    ):

        train_df, test_df = split_by_cell(
            df,
            test_cell
        )

        best_params, inner_score = (
            search_best_params(
                train_df[features],
                train_df[TARGET],
                train_df["cell_id"],
                n_iter=n_iter
            )
        )

        # The blend's XGBoost member carries the searched
        # parameters; ElasticNet runs at its defaults.
        model = BlendRegressor(
            xgb_params={
                key.replace("model__", ""):
                    value
                for key, value in best_params.items()
            }
        )

        model.fit(
            train_df[features],
            train_df[TARGET]
        )

        prediction = model.predict(
            test_df[features]
        )

        mae, rmse, r2 = score(
            test_df[TARGET],
            prediction
        )

        rows.append({
            "model": "Blend_nested",
            "test_cell": test_cell,
            "MAE": mae,
            "RMSE": rmse,
            "R2": r2,
            "inner_mae": -inner_score
        })

        frame = test_df.copy()

        frame[PREDICTION_COLUMN] = (
            prediction
        )

        frame["residual"] = (
            frame[TARGET] - prediction
        )

        predictions.append(frame)

    return rows, predictions


def optimistic_loco(
    df,
    features,
    n_iter=N_ITERATIONS,
    search_result=None
):
    """
    Search scored on the folds it tuned against.

    Reported only to quantify the optimism in the nested
    score. These numbers are not a valid generalisation
    estimate.

    search_result lets the caller pass an already-run search
    over the same data (the final all-cells model needs the
    same one), so the search runs once instead of twice.
    """

    if search_result is None:

        search_result = search_best_params(
            df[features],
            df[TARGET],
            df["cell_id"],
            n_iter=n_iter
        )

    best_params, best_score = search_result

    rows = []

    for test_cell in sorted(
        df["cell_id"].unique()
    ):

        train_df, test_df = split_by_cell(
            df,
            test_cell
        )

        model = BlendRegressor(
            xgb_params={
                key.replace("model__", ""):
                    value
                for key, value in best_params.items()
            }
        )

        model.fit(
            train_df[features],
            train_df[TARGET]
        )

        prediction = model.predict(
            test_df[features]
        )

        mae, rmse, r2 = score(
            test_df[TARGET],
            prediction
        )

        rows.append({
            "model": "Blend_optimistic",
            "test_cell": test_cell,
            "MAE": mae,
            "RMSE": rmse,
            "R2": r2,
            "inner_mae": -best_score
        })

    return rows


def feature_importance(model, features):
    """
    Gain-based importance for the fitted XGBoost step.
    """

    booster = model.named_steps["model"]

    importance = booster.feature_importances_

    frame = pd.DataFrame({
        "feature": features,
        "importance": importance
    })

    frame["rank"] = frame[
        "importance"
    ].rank(ascending=False).astype(int)

    return frame.sort_values(
        "importance",
        ascending=False
    ).reset_index(drop=True)


def bias_report(predictions):
    """
    Per-cell error bias and the range each model covers.

    A model that never predicts below its training floor
    cannot represent a cell that fades harder than anything
    it has seen, which is the failure this exposes.
    """

    rows = []

    for frame in predictions:

        cell = frame["cell_id"].iloc[0]

        actual = frame[TARGET]
        predicted = frame[PREDICTION_COLUMN]

        rows.append({
            "test_cell": cell,
            "MAE": mean_absolute_error(
                actual,
                predicted
            ),
            "bias": (predicted - actual).mean(),
            "actual_min": actual.min(),
            "actual_max": actual.max(),
            "predicted_min": predicted.min(),
            "predicted_max": predicted.max(),
            "under_prediction": float(
                (actual - predicted).clip(
                    lower=0
                ).sum()
            )
        })

    return pd.DataFrame(rows)


def main():

    if not DATA_FILE.exists():

        raise FileNotFoundError(
            f"{DATA_FILE} not found.\n"
            "Run load_data.py and preprocessing.py first."
        )

    df_all = pd.read_csv(DATA_FILE)

    # The baseline ladder, nested search, and per-cell models are for G1
    # (the 4 NASA cells), preserving the verified LOCO benchmarks and gate limits.
    from load_data import CELLS_G1
    df = df_all[df_all["cell_id"].isin(CELLS_G1)].copy()

    _, _, features = make_xy(
        df,
        TARGET
    )

    features, dropped = drop_constant_features(
        df[features]
    )

    MODEL_DIR.mkdir(
        exist_ok=True,
        parents=True
    )

    RESULT_DIR.mkdir(
        exist_ok=True,
        parents=True
    )

    print(
        "\n========== SOH DATASET =========="
    )

    print(
        f"\nRows     : {len(df)}"
    )

    print(
        f"Features : {len(features)}"
    )

    if dropped:

        print(
            "Dropped (no variation in the "
            f"training cells): {dropped}"
        )

    print(
        "\nPer-cell SOH range:"
    )

    print(
        df.groupby("cell_id")[TARGET]
        .agg(["count", "min", "max"])
        .round(2)
    )

    # ------------------------------------------------------------
    # 1. Baseline ladder under an identical protocol
    # ------------------------------------------------------------

    print(
        "\n\n========== BASELINE LADDER (leave-one-cell-out) =========="
    )

    all_rows = []
    all_baselines = []
    default_predictions = []

    for model_name in MODEL_NAMES:

        rows, predictions, baselines = loco_evaluate(
            df,
            TARGET,
            features,
            model_name,
            PREDICTION_COLUMN,
            with_baselines=(model_name == MODEL_NAMES[0])
        )

        all_rows.extend(rows)
        all_baselines.extend(baselines)

        if model_name == "XGBoost":

            default_predictions = predictions

    # ── Coulomb (physics) baseline ──
    # No ML, no train/test split: uses each cell's own first
    # discharge as the reference, so it is not a held-out score
    # in the LOCO sense.  It shows what a simple formula can do.
    coulomb_rows, _ = coulomb_baseline(df, TARGET)
    all_rows.extend(coulomb_rows)

    baseline_frame = pd.DataFrame(
        all_rows
    )

    print(
        baseline_frame
        .groupby("model")[["MAE", "RMSE", "R2"]]
        .mean()
        .round(4)
        .sort_values("MAE")
    )

    null_frame = pd.DataFrame(
        all_baselines
    )

    null_summary = null_frame.groupby(
        "baseline"
    )["MAE"].mean().round(4).sort_values()

    print(
        "\nConstant baselines (mean MAE):"
    )

    print(null_summary)

    # ------------------------------------------------------------
    # 2. Hyperparameter search, honest and optimistic
    # ------------------------------------------------------------

    print(
        "\n\n========== HYPERPARAMETER SEARCH =========="
    )

    print(
        f"\nRunning nested leave-one-cell-out "
        f"search, n_iter={N_ITERATIONS}."
    )

    print(
        "This takes a few minutes."
    )

    nested_rows, nested_predictions = nested_loco(
        df,
        features
    )

    print(
        "Running the optimistic comparison."
    )

    # One search, two consumers: the optimistic estimate below
    # and the final all-cells model further down need the same
    # parameters (same data, same LOCO folds, same seed). It
    # used to run twice; that cost one full extra search.
    final_params, final_score = search_best_params(
        df[features],
        df[TARGET],
        df["cell_id"]
    )

    optimistic_rows = optimistic_loco(
        df,
        features,
        search_result=(final_params, final_score)
    )

    nested_frame = pd.DataFrame(
        nested_rows
    )

    optimistic_frame = pd.DataFrame(
        optimistic_rows
    )

    print(
        "\nPer-cell results:"
    )

    print(
        pd.concat([
            nested_frame,
            optimistic_frame
        ], ignore_index=True)
        .round(4)
        .to_string(index=False)
    )

    nested_mae = nested_frame["MAE"].mean()

    optimistic_mae = optimistic_frame[
        "MAE"
    ].mean()

    print(
        f"\nNested (honest)   mean MAE : {nested_mae:.4f}"
    )

    print(
        f"Optimistic        mean MAE : {optimistic_mae:.4f}"
    )

    print(
        f"Optimism from tuning on the test folds : "
        f"{optimistic_mae - nested_mae:+.4f}"
    )

    # ------------------------------------------------------------
    # 3. Per-cell diagnosis
    # ------------------------------------------------------------

    print(
        "\n\n========== PER-CELL DIAGNOSIS (nested model) =========="
    )

    bias = bias_report(
        nested_predictions
    )

    print(bias.round(3).to_string(index=False))

    worst = bias.loc[
        bias["MAE"].idxmax()
    ]

    print(
        f"\nWorst cell: {worst['test_cell']} "
        f"with MAE {worst['MAE']:.2f}."
    )

    print(
        f"It degrades to {worst['actual_min']:.1f}% SOH "
        f"but the model only ever predicts down to "
        f"{worst['predicted_min']:.1f}%."
    )

    print(
        "A tree ensemble trained on cells that never fade "
        "this hard"
    )

    print(
        "extrapolates by averaging toward the training "
        "floor, so"
    )

    print(
        "severe degradation is systematically "
        "under-predicted."
    )

    print(
        "This is a data-coverage limit, not a tuning "
        "problem: no"
    )

    print(
        "hyperparameter setting can invent a "
        "degradation the"
    )

    print(
        "training cells never exhibited."
    )

    # ------------------------------------------------------------
    # 4. Final model on all cells
    # ------------------------------------------------------------

    print(
        "\n\n========== FINAL MODEL (all cells) =========="
    )

    # Reuses the search run before the optimistic pass above;
    # identical inputs and seed, so identical parameters.
    final_model = BlendRegressor(
        xgb_params={
            key.replace("model__", ""):
                value
            for key, value in final_params.items()
        }
    )

    final_model.fit(
        df[features],
        df[TARGET]
    )

    print(
        "Parameters chosen with leave-one-cell-out "
        "over all four cells:"
    )

    for key, value in sorted(
        final_params.items()
    ):

        print(f"  {key:28s} {value}")

    print(
        f"\nLeave-one-cell-out MAE of these "
        f"parameters (optimistic): {-final_score:.4f}"
    )

    # ------------------------------------------------------------
    # 5. Persist artefacts
    # ------------------------------------------------------------

    for frame in default_predictions:

        cell = frame["cell_id"].iloc[0]

        rounded(frame).to_csv(
            RESULT_DIR / f"soh_predictions_{cell}.csv",
            index=False
        )

        # Each per-cell model is trained on the other three
        # cells, matching what the leave-one-cell-out score
        # in soh_results.csv was produced from.
        held_out_model = build_model(
            "XGBoost"
        )

        train_df, _ = split_by_cell(
            df,
            cell
        )

        held_out_model.fit(
            train_df[features],
            train_df[TARGET]
        )

        joblib.dump(
            {
                "model": held_out_model,
                "features": features,
                "target": TARGET,
                "trained_on": f"all cells except {cell}"
            },
            MODEL_DIR / f"soh_xgb_{cell}.joblib"
        )

    joblib.dump(
        {
            "model": final_model,
            "features": features,
            "target": TARGET,
            "trained_on": "all cells",
            "params": final_params
        },
        MODEL_DIR / "soh_xgb_all.joblib"
    )

    importance = feature_importance(
        final_model.xgb_,
        features
    )

    print(
        "\n\n========== FEATURE IMPORTANCE (all-cell model) =========="
    )

    print(
        importance.round(5)
        .to_string(index=False)
    )

    # soh_results.csv keeps its original four columns so
    # existing consumers keep working. It holds the
    # hand-tuned default model, not the searched one.
    rounded(baseline_frame[
        baseline_frame["model"] == "XGBoost"
    ][
        ["test_cell", "MAE", "RMSE", "R2"]
    ]).to_csv(
        RESULT_DIR / "soh_results.csv",
        index=False
    )

    rounded(baseline_frame).to_csv(
        RESULT_DIR / "soh_baselines.csv",
        index=False
    )

    rounded(null_frame).to_csv(
        RESULT_DIR / "soh_null_baselines.csv",
        index=False
    )

    rounded(nested_frame).to_csv(
        RESULT_DIR / "soh_nested_results.csv",
        index=False
    )

    rounded(importance).to_csv(
        RESULT_DIR / "soh_feature_importance.csv",
        index=False
    )

    rounded(bias).to_csv(
        RESULT_DIR / "soh_bias_report.csv",
        index=False
    )

    print(
        "\n\n========== SUMMARY =========="
    )

    print(
        f"\nHand-tuned XGBoost  MAE : "
        f"{baseline_frame[baseline_frame['model'] == 'XGBoost']['MAE'].mean():.4f}"
    )

    print(
        f"Nested-search XGBoost MAE: {nested_mae:.4f}"
    )

    # null_summary is sorted ascending by MAE, so min() is the
    # strongest baseline and max() the weakest. The oracle is
    # neither to rank against nor to beat: it is a ceiling.
    # These three lines used to take min()/max() blindly,
    # which printed the oracle as the "best constant
    # baseline" and the weakest constant as the "ceiling".
    # Each label now names the baseline it actually refers
    # to, so a reader cannot misread the comparison.

    real_baselines = null_summary.drop(
        ["oracle_cell_mean"],
        errors="ignore"
    )

    print(
        f"Best honest baseline     : "
        f"{real_baselines.min():.4f} ({real_baselines.idxmin()})"
    )

    print(
        f"Oracle ceiling           : "
        f"{null_summary['oracle_cell_mean']:.4f} "
        f"(oracle_cell_mean)"
    )

    print(
        "\nSaved models to",
        MODEL_DIR
    )

    print(
        "Saved results to",
        RESULT_DIR
    )

    # ────────────────────────────────────────────────────
    # 6. Demo model — excludes the cells the publisher replays
    # ────────────────────────────────────────────────────
    # publisher.py replays B0005, B0007, B0018.  A model trained
    # on those cells and then asked to predict them is showing
    # in-sample performance, which is dishonest.  soh_xgb_demo
    # trains on everything EXCEPT the demo cells.

    DEMO_CELLS = {"B0005", "B0007", "B0018"}
    demo_train = df[~df["cell_id"].isin(DEMO_CELLS)]

    if len(demo_train) > 0 and len(demo_train["cell_id"].unique()) >= 1:
        demo_model = build_model("XGBoost")
        demo_model.fit(
            demo_train[features],
            demo_train[TARGET]
        )

        demo_bundle = {
            "model": demo_model,
            "features": features,
            "target": TARGET,
            "trained_on": sorted(
                demo_train["cell_id"].unique().tolist()
            ),
            "excluded": sorted(DEMO_CELLS),
        }

        joblib.dump(
            demo_bundle,
            MODEL_DIR / "soh_xgb_demo.joblib"
        )

        # Version-proof export: UBJ booster + JSON metadata
        _save_portable(
            demo_model, features, TARGET,
            demo_bundle["trained_on"],
            MODEL_DIR / "soh_xgb_demo"
        )

        print(
            f"\nDemo model saved (trained on "
            f"{sorted(demo_train['cell_id'].unique().tolist())}, "
            f"excluding {sorted(DEMO_CELLS)})"
        )

    # Portable UBJ export is XGBoost-only; the blend has no
    # single booster to export. The joblib bundle above is the
    # deployable artifact for the all-cell model.
    if hasattr(final_model, "named_steps"):

        _save_portable(
            final_model, features, TARGET,
            sorted(df["cell_id"].unique().tolist()),
            MODEL_DIR / "soh_xgb_all"
        )

    else:

        print(
            "\nPortable UBJ export skipped: the final model "
            "is the XGBoost+ElasticNet blend; use the joblib "
            "bundle."
        )


def _save_portable(
    pipeline, features, target, trained_on, prefix, feature_ranges=None
):
    """
    Save a version-proof copy: XGBoost UBJ booster + JSON
    metadata (features, target, trained_on, library versions).

    The UBJ format is portable across XGBoost versions,
    unlike the full Pipeline pickle which embeds the native
    booster binary. The imputer medians are saved too so
    the pipeline can be reconstructed.

    ``feature_ranges`` (optional) is the per-feature
    ``[min, max]`` the model was trained across; the estimator
    refuses an input outside that range widened by
    ``features.WINDOW_RANGE_SLACK`` rather than scoring it.
    """

    import json
    import platform
    import sklearn
    import xgboost

    prefix = Path(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)

    # Extract booster
    booster = pipeline.named_steps["model"].get_booster()
    booster.save_model(str(prefix) + ".ubj")

    # Extract imputer medians
    imputer = pipeline.named_steps["imputer"]
    medians = imputer.statistics_.tolist()

    meta = {
        "features": list(features),
        "target": target,
        "trained_on": list(trained_on),
        "imputer_medians": medians,
        "library_versions": {
            "xgboost": xgboost.__version__,
            "scikit-learn": sklearn.__version__,
            "python": platform.python_version(),
        },
    }

    if feature_ranges is not None:

        meta["feature_ranges"] = {
            name: [float(bounds[0]), float(bounds[1])]
            for name, bounds in feature_ranges.items()
        }

    with open(str(prefix) + ".json", "w") as f:
        json.dump(meta, f, indent=2)


if __name__ == "__main__":

    main()

