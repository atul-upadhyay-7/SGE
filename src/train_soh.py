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

import joblib
import pandas as pd

from sklearn.metrics import (
    mean_absolute_error
)
from sklearn.model_selection import (
    LeaveOneGroupOut,
    RandomizedSearchCV
)

from evaluation import (
    MODEL_NAMES,
    build_model,
    loco_evaluate,
    score,
    split_by_cell
)

from features import (
    drop_constant_features,
    make_xy
)


DATA_FILE = Path(
    "data/processed/nasa_ml_dataset.csv"
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
N_ITERATIONS = 20

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
        n_jobs=1,
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

        model = build_model(
            "XGBoost",
            **{
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
            "model": "XGBoost_nested",
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
    n_iter=N_ITERATIONS
):
    """
    Search scored on the folds it tuned against.

    Reported only to quantify the optimism in the nested
    score. These numbers are not a valid generalisation
    estimate.
    """

    best_params, best_score = search_best_params(
        df[features],
        df[TARGET],
        df["cell_id"],
        n_iter=n_iter
    )

    rows = []

    for test_cell in sorted(
        df["cell_id"].unique()
    ):

        _, test_df = split_by_cell(
            df,
            test_cell
        )

        model = build_model(
            "XGBoost",
            **{
                key.replace("model__", ""):
                    value
                for key, value in best_params.items()
            }
        )

        model.fit(
            df[features],
            df[TARGET]
        )

        prediction = model.predict(
            test_df[features]
        )

        mae, rmse, r2 = score(
            test_df[TARGET],
            prediction
        )

        rows.append({
            "model": "XGBoost_optimistic",
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

    df = pd.read_csv(DATA_FILE)

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

    optimistic_rows = optimistic_loco(
        df,
        features
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

    final_params, final_score = search_best_params(
        df[features],
        df[TARGET],
        df["cell_id"]
    )

    final_model = build_model(
        "XGBoost",
        **{
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

        frame.to_csv(
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
        final_model,
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
    baseline_frame[
        baseline_frame["model"] == "XGBoost"
    ][
        ["test_cell", "MAE", "RMSE", "R2"]
    ].to_csv(
        RESULT_DIR / "soh_results.csv",
        index=False
    )

    baseline_frame.to_csv(
        RESULT_DIR / "soh_baselines.csv",
        index=False
    )

    null_frame.to_csv(
        RESULT_DIR / "soh_null_baselines.csv",
        index=False
    )

    nested_frame.to_csv(
        RESULT_DIR / "soh_nested_results.csv",
        index=False
    )

    importance.to_csv(
        RESULT_DIR / "soh_feature_importance.csv",
        index=False
    )

    bias.to_csv(
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

    print(
        f"Best constant baseline   : "
        f"{null_summary.min():.4f} ({null_summary.idxmin()})"
    )

    print(
        f"Oracle ceiling          : "
        f"{null_summary.max():.4f} ({null_summary.idxmax()})"
    )

    print(
        "\nSaved models to",
        MODEL_DIR
    )

    print(
        "Saved results to",
        RESULT_DIR
    )


if __name__ == "__main__":

    main()
