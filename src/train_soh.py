"""
train_soh.py
-------------
Train State-of-Health (SOH) estimation model.
"""


from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

from sklearn.metrics import (
    mean_absolute_error,
    mean_squared_error,
    r2_score
)

from xgboost import XGBRegressor

from features import (
    make_xy,
    split_by_cell
)


DATA_FILE = Path(
    "data/processed/nasa_ml_dataset.csv"
)

MODEL_DIR = Path("models")

RESULT_DIR = Path(
    "data/processed"
)


def train_model(
    df,
    test_cell
):

    train_df, test_df = split_by_cell(
        df,
        test_cell
    )

    X_train, y_train, features = (
        make_xy(
            train_df,
            "soh"
        )
    )

    X_test, y_test, _ = (
        make_xy(
            test_df,
            "soh"
        )
    )

    model = Pipeline([

        (
            "imputer",
            SimpleImputer(
                strategy="median"
            )
        ),

        (
            "xgb",
            XGBRegressor(

                n_estimators=500,

                max_depth=5,

                learning_rate=0.03,

                subsample=0.85,

                colsample_bytree=0.85,

                objective="reg:squarederror",

                random_state=42,

                n_jobs=-1
            )
        )
    ])

    model.fit(
        X_train,
        y_train
    )

    prediction = model.predict(
        X_test
    )

    mae = mean_absolute_error(
        y_test,
        prediction
    )

    rmse = np.sqrt(
        mean_squared_error(
            y_test,
            prediction
        )
    )

    r2 = r2_score(
        y_test,
        prediction
    )

    results = {

        "test_cell": test_cell,

        "MAE": mae,

        "RMSE": rmse,

        "R2": r2
    }

    prediction_df = test_df.copy()

    prediction_df[
        "predicted_soh"
    ] = prediction

    return (
        model,
        features,
        results,
        prediction_df
    )


def main():

    df = pd.read_csv(
        DATA_FILE
    )

    cells = sorted(
        df["cell_id"].unique()
    )

    MODEL_DIR.mkdir(
        exist_ok=True
    )

    results = []

    for test_cell in cells:

        print(
            "\n=============================="
        )

        print(
            f"Testing on {test_cell}"
        )

        print(
            "=============================="
        )

        (
            model,
            features,
            result,
            predictions
        ) = train_model(
            df,
            test_cell
        )

        print(
            f"MAE  : {result['MAE']:.4f}%"
        )

        print(
            f"RMSE : {result['RMSE']:.4f}%"
        )

        print(
            f"R²   : {result['R2']:.4f}"
        )

        results.append(
            result
        )

        joblib.dump(

            {
                "model": model,
                "features": features,
                "target": "soh"
            },

            MODEL_DIR /
            f"soh_xgb_{test_cell}.joblib"
        )

        predictions.to_csv(

            RESULT_DIR /
            f"soh_predictions_{test_cell}.csv",

            index=False
        )

    results_df = pd.DataFrame(
        results
    )

    results_df.to_csv(

        RESULT_DIR /
        "soh_results.csv",

        index=False
    )

    print(
        "\n\n========== FINAL RESULT =========="
    )

    print(
        results_df
    )

    print(
        "\nAverage:"
    )

    print(
        results_df[
            [
                "MAE",
                "RMSE",
                "R2"
            ]
        ].mean()
    )


if __name__ == "__main__":

    main()