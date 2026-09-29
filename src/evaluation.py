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

from sklearn.ensemble import (
    RandomForestRegressor
)
from sklearn.impute import SimpleImputer
from sklearn.linear_model import (
    LinearRegression,
    Ridge
)
from sklearn.metrics import (
    mean_absolute_error,
    mean_squared_error,
    r2_score
)
from sklearn.pipeline import Pipeline

from xgboost import XGBRegressor


# Defaults tuned by hand and used as the fixed comparison
# point before any search is run.
DEFAULT_PARAMS = {

    "Linear": {},

    "Ridge": {
        "alpha": 1.0
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


def build_model(name, **overrides):
    """
    Return an unfitted pipeline for a named estimator.

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
        "RandomForest": RandomForestRegressor,
        "XGBoost": XGBRegressor

    }[name]

    return Pipeline([
        (
            "imputer",
            SimpleImputer(
                strategy="median"
            )
        ),
        ("model", estimator_class(**params))
    ])


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


def assert_no_leakage(
    features,
    leaky_columns
):
    """
    Guard against a target-derived column reaching the
    estimator. Failing loudly here is much cheaper than
    silently reporting an inflated score.
    """

    leaked = sorted(
        set(features) & set(leaky_columns)
    )

    if leaked:

        raise ValueError(
            "Target-derived columns reached the "
            f"estimator: {leaked}"
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

    rows = []
    predictions = []
    baselines = []

    for test_cell in sorted(
        df["cell_id"].unique()
    ):

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

        rows.append({
            "model": model_name,
            "test_cell": test_cell,
            "MAE": mae,
            "RMSE": rmse,
            "R2": r2
        })

        frame = test_df.copy()

        frame[prediction_column] = (
            prediction
        )

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
                "test_cell": test_cell,
                "baseline": name,
                "MAE": baseline_mae,
                "value": value
            })

    return rows, predictions, baselines
