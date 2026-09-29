"""
preprocessing.py
-----------------
Clean, filter, and normalize raw battery cycle data.
"""


from pathlib import Path

import numpy as np
import pandas as pd


INPUT_FILE = Path(
    "data/processed/nasa_cycle_features_raw.csv"
)

OUTPUT_FILE = Path(
    "data/processed/nasa_ml_dataset.csv"
)


def create_targets(df):

    df = df.copy()

    df = df.sort_values(
        ["cell_id", "cycle"]
    ).reset_index(drop=True)

    # ------------------------------------------------
    # 1. Reference capacity
    # ------------------------------------------------

    reference_capacity = (
        df.groupby("cell_id")
        ["capacity_ah"]
        .transform("first")
    )

    df["reference_capacity_ah"] = (
        reference_capacity
    )

    # ------------------------------------------------
    # 2. SOH
    # ------------------------------------------------

    df["soh"] = (
        df["capacity_ah"]
        / df["reference_capacity_ah"]
        * 100
    )

    # ------------------------------------------------
    # 3. Capacity fade
    # ------------------------------------------------

    df["capacity_fade_pct"] = (
        100 - df["soh"]
    )

    # ------------------------------------------------
    # 4. Cycle-to-cycle capacity change
    # ------------------------------------------------

    df["capacity_change_ah"] = (
        df.groupby("cell_id")
        ["capacity_ah"]
        .diff()
    )

    # ------------------------------------------------
    # 5. SoH change
    # ------------------------------------------------

    df["soh_change_pct"] = (
        df.groupby("cell_id")
        ["soh"]
        .diff()
    )

    # ------------------------------------------------
    # 6. RUL
    # ------------------------------------------------

    # Initial prototype:
    # remaining cycles until the final recorded
    # discharge cycle.

    final_cycle = (
        df.groupby("cell_id")
        ["cycle"]
        .transform("max")
    )

    df["eol_cycle_observed"] = (
        final_cycle
    )

    df["rul_cycles"] = (
        df["eol_cycle_observed"]
        - df["cycle"]
    )

    # ------------------------------------------------
    # Clean invalid values
    # ------------------------------------------------

    df = df.replace(
        [np.inf, -np.inf],
        np.nan
    )

    df = df.dropna(
        subset=[
            "soh",
            "rul_cycles"
        ]
    )

    return df


def main():

    if not INPUT_FILE.exists():

        raise FileNotFoundError(
            f"{INPUT_FILE} not found.\n"
            "Run load_data.py first."
        )

    df = pd.read_csv(
        INPUT_FILE
    )

    df = create_targets(df)

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    df.to_csv(
        OUTPUT_FILE,
        index=False
    )

    print(
        "\nSaved:",
        OUTPUT_FILE
    )

    print(
        "\nDataset shape:",
        df.shape
    )

    print(
        "\nBattery summary:"
    )

    print(
        df.groupby("cell_id")
        .agg(
            cycles=("cycle", "count"),
            initial_soh=("soh", "first"),
            final_soh=("soh", "last"),
            max_rul=("rul_cycles", "max")
        )
    )


if __name__ == "__main__":

    main()