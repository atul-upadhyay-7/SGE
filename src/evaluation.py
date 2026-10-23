"""
evaluation.py
-------------
Shared model construction, scoring and leave-one-cell-out
validation used by the training scripts.

Every model in this project is scored the same way: one cell is
held out entirely, the model is trained on the remaining cells,
and it is asked to predict a cell it has never seen. This is
deliberately the hardest available protocol, because two cells
of the same chemistry still differ in the shape and timing of
their degradation, so a model that scores well here is measuring
something real rather than memorising a cell identity.
"""

import numpy as np
from joblib import Parallel, delayed
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, LinearRegression, Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

import features as _features

# Defaults tuned by hand and used as the fixed comparison
# point before any search is run.
DEFAULT_PARAMS = {

    "Linear": {},

    "Ridge": {
        "alpha": 1.0
    },

    "ElasticNet": {
        "alpha": 0.1,
        "l1_ratio": 0.5,
        "max_iter": 10000
    },

    "RandomForest": {
        "n_estimators": 400,
        "min_samples_leaf": 3,
        "random_state": 42,
        "n_jobs": -1
    },

    "XGBoost": {
        "n_estimators": 500,
        "max_depth": 5,
        "learning_rate": 0.03,
        "subsample": 0.85,
        "colsample_bytree": 0.85,
        "objective": "reg:squarederror",
        "random_state": 42,
        "n_jobs": -1
    }

}

MODEL_NAMES = list(DEFAULT_PARAMS)

# Models whose per-cell fits are worth running in parallel.
_PARALLEL_MODELS = {"RandomForest", "XGBoost"}

# Models that benefit from feature scaling: linear family.
_SCALED_MODELS = {"Linear", "Ridge", "ElasticNet"}


def build_model(name, **overrides):
    """
    Return an unfitted pipeline for a named estimator.

    Linear models (Linear, Ridge, ElasticNet) get a
    StandardScaler after the imputer so that regularisation
    and coefficient magnitudes are meaningful.

    overrides are merged over the defaults for that name.
    """

    if name not in DEFAULT_PARAMS:

        raise ValueError(
            f"Unknown model: {name}. "
            f"Choose from {MODEL_NAMES}."
        )

    params = dict(
        DEFAULT_PARAMS[name]
    )

    params.update(overrides)

    estimator_class = {

        "Linear": LinearRegression,
        "Ridge": Ridge,
        "ElasticNet": ElasticNet,
        "RandomForest": RandomForestRegressor,
        "XGBoost": XGBRegressor

    }[name]

    steps = [
        (
            "imputer",
            SimpleImputer(
                strategy="median"
            )
        ),
    ]

    if name in _SCALED_MODELS:
        steps.append(("scaler", StandardScaler()))

    steps.append(("model", estimator_class(**params)))

    return Pipeline(steps)


def score(y_true, y_pred):
    """
    Return MAE, RMSE and R2 for a prediction.
    """

    mae = mean_absolute_error(
        y_true,
        y_pred
    )

    rmse = np.sqrt(
        mean_squared_error(
            y_true,
            y_pred
        )
    )

    r2 = r2_score(
        y_true,
        y_pred
    )

    return mae, rmse, r2


def split_by_cell(df, test_cell):
    """
    Split rows into every cell except test_cell, and test_cell.
    """

    train = df[
        df["cell_id"] != test_cell
    ].copy()

    test = df[
        df["cell_id"] == test_cell
    ].copy()

    return train, test


# Columns that are functions of the target, of the cell's
# lifetime, or of future cycles. None of these may reach a
# predictive estimator, because none of them can be observed
# for a cell the model has not already seen.
#
# Imported from features.py rather than redefined here. An
# earlier version of this file carried its own copy, which
# meant make_xy and assert_no_leakage were guarding against
# two lists that could quietly diverge: adding a leaky column
# to one and not the other would weaken the guard without any
# test failing. train_rul.py similarly keeps only the
# narrower RUL-specific subset it legitimately needs.
LEAKY_FEATURES = _features.LEAKY_FEATURES


def assert_no_leakage(
    features,
    leaky_columns=None
):
    """
    Guard against a target-derived column reaching the
    estimator. Failing loudly here is much cheaper than
    silently reporting an inflated score.

    leaky_columns defaults to the canonical LEAKY_FEATURES
    list. Callers that legitimately need a narrower set
    (RUL predicts a function of the EOL cycle, so it
    excludes the RUL/EOL columns from its own list) pass it
    explicitly.
    """

    if leaky_columns is None:

        leaky_columns = LEAKY_FEATURES

    leaked = sorted(
        set(features) & set(leaky_columns)
    )

    if leaked:

        raise ValueError(
            "Target-derived columns reached the "
            f"estimator: {leaked}\n"
            "These are functions of the target, of the "
            "cell's lifetime, or of future cycles, and "
            "cannot be observed for an unseen cell.\n"
            "Remove them from the feature list, or narrow "
            "the leaky_columns argument if this target is "
            "legitimately defined in terms of them."
        )


def null_baselines(
    df,
    target,
    train_df,
    test_df
):
    """
    Constant predictors used to interpret a model score.

    constant_train_mean
        The training cells' mean target. This is the
        baseline a model must genuinely beat.
    constant_global_mean
        The whole dataset's mean target.
    oracle_cell_mean
        The test cell's own mean target. No cell-agnostic
        model can reach this, because it is handed the one
        quantity the model can never observe: the held-out
        cell's actual lifetime. It is the effective ceiling.
    """

    return {

        "constant_train_mean": (
            mean_absolute_error(
                test_df[target],
                np.full(
                    len(test_df),
                    train_df[target].mean()
                )
            ),
            train_df[target].mean()
        ),

        "constant_global_mean": (
            mean_absolute_error(
                test_df[target],
                np.full(
                    len(test_df),
                    df[target].mean()
                )
            ),
            df[target].mean()
        ),

        "oracle_cell_mean": (
            mean_absolute_error(
                test_df[target],
                np.full(
                    len(test_df),
                    test_df[target].mean()
                )
            ),
            test_df[target].mean()
        )

    }


# Weight on the XGBoost member of the SOH blend. The linear
# member carries extrapolation beyond the training SOH range:
# trees saturate at the lowest target they saw, and B0006
# fades well past it (the bias report showed predictions
# flooring at 70.8 while the cell reached 57.3). The weight
# was picked on the four G1 cells; the nested-protocol sweep
# shows any weight in 0.1-0.4 beats either member alone, so
# the exact value is not knife-edge.
BLEND_XGB_WEIGHT = 0.3


class BlendRegressor:
    """
    Fixed-weight blend of XGBoost and ElasticNet.

    fit/predict mirror the sklearn estimators it replaces, so
    the training and serving code paths are unchanged. Each
    member is a full build_model pipeline (imputer, plus the
    scaler for the linear member), so the blend takes the raw
    feature frame.
    """

    def __init__(
        self,
        xgb_params=None,
        xgb_weight=BLEND_XGB_WEIGHT
    ):
        self.xgb_params = dict(xgb_params or {})
        self.xgb_weight = xgb_weight

    def fit(self, X, y):
        self.xgb_ = build_model(
            "XGBoost", **self.xgb_params
        )
        self.en_ = build_model("ElasticNet")
        self.xgb_.fit(X, y)
        self.en_.fit(X, y)
        return self

    def predict(self, X):
        return (
            self.xgb_weight * self.xgb_.predict(X)
            + (1.0 - self.xgb_weight)
            * self.en_.predict(X)
        )



def loco_evaluate(
    df,
    target,
    features,
    model_name,
    prediction_column,
    leaky_columns=(),
    with_baselines=True
):
    """
    Leave-one-cell-out evaluation for one model.

    Returns a tuple of
        rows        per-cell metric records
        predictions per-cell frames with the prediction column
        baselines   per-cell null baseline records
    """

    assert_no_leakage(
        features,
        leaky_columns
    )

    # Per-cell fits are independent, so the heavy models are
    # evaluated one cell per worker. XGBoost and RandomForest
    # are deterministic in their thread count (fixed data,
    # fixed random_state), so the rows, predictions and
    # baselines are identical to the sequential pass; only the
    # wall-clock time changes. Light linear models stay
    # sequential: their fits are milliseconds and process
    # dispatch would cost more than the fit.
    cells = sorted(df["cell_id"].unique())

    n_jobs = -1 if model_name in _PARALLEL_MODELS else 1

    results = Parallel(n_jobs=n_jobs)(
        delayed(_loco_one_cell)(
            df,
            test_cell,
            target,
            features,
            model_name,
            prediction_column,
            with_baselines
        )
        for test_cell in cells
    )

    rows = [row for row, _, _ in results]
    predictions = [frame for _, frame, _ in results]
    baselines = [
        record
        for _, _, cell_baselines in results
        for record in cell_baselines
    ]

    return rows, predictions, baselines


def _loco_one_cell(
    df,
    test_cell,
    target,
    features,
    model_name,
    prediction_column,
    with_baselines
):
    """
    The loco_evaluate work for one held-out cell, kept as a
    module-level function so joblib can pickle it to workers.
    """

    train_df, test_df = split_by_cell(
        df,
        test_cell
    )

    model = build_model(
        model_name
    )

    model.fit(
        train_df[features],
        train_df[target]
    )

    prediction = model.predict(
        test_df[features]
    )

    mae, rmse, r2 = score(
        test_df[target],
        prediction
    )

    row = {
        "model": model_name,
        "test_cell": test_cell,
        "MAE": mae,
        "RMSE": rmse,
        "R2": r2
    }

    frame = test_df.copy()

    frame[prediction_column] = (
        prediction
    )

    cell_baselines = []

    if with_baselines:

        for name, (
            baseline_mae,
            value
        ) in null_baselines(
            df,
            target,
            train_df,
            test_df
        ).items():

            cell_baselines.append({
                "test_cell": test_cell,
                "baseline": name,
                "MAE": baseline_mae,
                "value": value
            })

    return row, frame, cell_baselines


def coulomb_baseline(df, target="soh"):
    """
    Physics-only SOH estimate per cell, no ML needed.

    SOH_coulomb = (duration × |current_mean|) of this cycle
                ÷ (duration × |current_mean|) of the cell's
                  first discharge × 100.

    For constant-current discharges the numerator is
    proportional to the extracted charge, so this is
    essentially coulomb counting against the first cycle.

    Returns a list of per-cell result dicts and a list of
    per-cell DataFrames with the 'coulomb_soh' column.
    """

    rows = []
    frames = []

    for cell_id, group in df.groupby("cell_id"):

        group = group.sort_values("cycle").copy()

        q = (
            group["discharge_duration_s"].abs()
            * group["current_mean"].abs()
        )

        q_first = q.iloc[0]

        if q_first == 0 or not np.isfinite(q_first):
            continue

        group["coulomb_soh"] = q / q_first * 100.0

        mae, rmse, r2 = score(
            group[target],
            group["coulomb_soh"]
        )

        rows.append({
            "model": "Coulomb",
            "test_cell": cell_id,
            "MAE": mae,
            "RMSE": rmse,
            "R2": r2
        })

        frames.append(group)

    return rows, frames


def split_by_group(df, test_group, group_col="group"):
    """
    Split rows into everything outside test_group and
    everything inside it.
    """

    mask = df[group_col] == test_group

    return df[~mask].copy(), df[mask].copy()


def logo_evaluate(
    df,
    target,
    features,
    model_name,
    prediction_column,
    group_col="group",
    leaky_columns=(),
    with_baselines=True
):
    """
    Leave-one-group-out evaluation for one model.

    Same interface as loco_evaluate but splits on group
    instead of cell_id, which is the correct protocol when
    cells within a group share operating conditions.

    Returns (rows, predictions, baselines) where rows is a
    list of per-group metric dicts with per-cell detail.
    """

    assert_no_leakage(
        features,
        leaky_columns
    )

    rows = []
    predictions = []
    baselines = []

    for test_group in sorted(
        df[group_col].unique()
    ):

        train_df, test_df = split_by_group(
            df,
            test_group,
            group_col
        )

        model = build_model(
            model_name
        )

        model.fit(
            train_df[features],
            train_df[target]
        )

        prediction = model.predict(
            test_df[features]
        )

        mae, rmse, r2 = score(
            test_df[target],
            prediction
        )

        rows.append({
            "model": model_name,
            "test_group": test_group,
            "MAE": mae,
            "RMSE": rmse,
            "R2": r2,
            "n_cells": test_df["cell_id"].nunique(),
            "n_cycles": len(test_df),
        })

        # Per-cell detail within the held-out group
        frame = test_df.copy()
        frame[prediction_column] = prediction
        predictions.append(frame)

        if not with_baselines:
            continue

        for name, (
            baseline_mae,
            value
        ) in null_baselines(
            df,
            target,
            train_df,
            test_df
        ).items():

            baselines.append({
                "test_group": test_group,
                "baseline": name,
                "MAE": baseline_mae,
                "value": value
            })

    return rows, predictions, baselines



