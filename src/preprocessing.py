"""
preprocessing.py
-----------------
Clean, filter, and normalize raw battery cycle data.
"""


from pathlib import Path

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from causal import add_causal_features


INPUT_FILE = Path(
    "data/processed/nasa_cycle_features_raw.csv"
)

OUTPUT_FILE = Path(
    "data/processed/nasa_ml_dataset.csv"
)


# A cell is considered at end of life once it retains this
# fraction of its beginning-of-life capacity. 80% is the
# conventional retirement threshold in the battery PHM
# literature.
EOL_SOH_THRESHOLD = 80.0

# Number of earliest cycles used for the "online" degradation
# rate. A battery management system can only ever estimate its
# fade rate from the cycles observed so far, so this column
# models what is actually computable in the field.
EARLY_SLOPE_WINDOW = 40


def ols_slope(x, y):
    """
    Least-squares slope of y on x.

    Returns NaN when the input is too short or degenerate
    rather than raising, because short groups are expected
    for cells that failed early.
    """

    x = np.asarray(
        x,
        dtype=float
    )

    y = np.asarray(
        y,
        dtype=float
    )

    valid = (
        np.isfinite(x)
        & np.isfinite(y)
    )

    x = x[valid]
    y = y[valid]

    if len(x) < 2:

        return np.nan

    variance = np.var(x)

    if not np.isfinite(variance) or variance <= 0:

        return np.nan

    return float(
        np.cov(x, y, bias=True)[0, 1] / variance
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
    # 6. End of life (deprecated) and RUL (physical)
    # ------------------------------------------------

    # DEPRECATED. Retained only so older prediction CSVs
    # keep their column layout. Do not train on this.
    #
    # It measures RUL against the last recorded cycle, so
    # within any single cell it reduces to (eol - cycle):
    # a perfect linear function of the cycle index, with
    # the test cell's own unknown lifetime as the
    # intercept. It therefore cannot transfer across cells,
    # and leave-one-cell-out validation collapses.

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
    # 7. Physical end of life and Remaining Useful Life
    # ------------------------------------------------

    # End of life is defined against capacity rather than
    # against the end of the test window, which makes the
    # target meaningful for a cell that is still healthy.
    eol_cycle = (
        df[df["soh"] < EOL_SOH_THRESHOLD]
        .groupby("cell_id")
        ["cycle"]
        .min()
    )

    df["eol_cycle_threshold"] = (
        df["cell_id"].map(eol_cycle)
    )

    # A cell that never falls below the threshold in the
    # recorded window has no observed end of life. Flagged
    # as not-pre-EOL so downstream code can drop it rather
    # than silently train on a negative RUL.

    has_eol = df["eol_cycle_threshold"].notna()

    df["is_pre_eol"] = (
        has_eol
        & (
            df["cycle"]
            <= df["eol_cycle_threshold"]
        )
    )

    df["rul_cycles_80"] = (
        df["eol_cycle_threshold"]
        - df["cycle"]
    ).where(df["is_pre_eol"])

    # ------------------------------------------------
    # 8. Degradation rate
    # ------------------------------------------------

    # The fade rate in %SOH per cycle. This is the physical
    # quantity that governs RUL, via
    #     remaining life = (soh - threshold) / rate
    # It is reported per cell and is directly interpretable
    # even where absolute RUL is not reliably predictable.

    full_slopes = {}
    early_slopes = {}

    for cell_id, group in df.groupby("cell_id"):

        life = group

        if group["is_pre_eol"].any():

            life = group[group["is_pre_eol"]]

        full_slopes[cell_id] = ols_slope(
            life["cycle"],
            life["soh"]
        )

        early = life.head(
            EARLY_SLOPE_WINDOW
        )

        early_slopes[cell_id] = ols_slope(
            early["cycle"],
            early["soh"]
        )

    df["soh_slope_pct_per_cycle"] = (
        df["cell_id"].map(full_slopes)
    )

    df["soh_slope_early_pct_per_cycle"] = (
        df["cell_id"].map(early_slopes)
    )

    # ------------------------------------------------
    # Clean invalid values
    # ------------------------------------------------

    df = df.replace(
        [np.inf, -np.inf],
        np.nan
    )

    # Only rows without a usable target are dropped. The
    # post-end-of-life rows are deliberately kept: they carry
    # valid SOH labels and are exactly the deep-fade region
    # the SOH model needs. rul_cycles_80 is NaN there, which
    # is how train_rul.py selects the pre-EOL subset.

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

    # causal load / delta / trailing-slope features, built by
    # the same class the streaming service runs
    df = add_causal_features(df)

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
            eol_cycle=("eol_cycle_threshold", "first"),
            pre_eol_rows=("is_pre_eol", "sum"),
            max_rul=("rul_cycles_80", "max"),
            fade_pct_per_cycle=(
                "soh_slope_pct_per_cycle",
                "first"
            ),
            early_fade_pct_per_cycle=(
                "soh_slope_early_pct_per_cycle",
                "first"
            )
        )
        .round(4)
    )

    print(
        "\nEOL threshold: "
        f"{EOL_SOH_THRESHOLD}% SOH"
    )

    print(
        "\nRows trainable for RUL "
        f"(pre-EOL): {int(df['is_pre_eol'].sum())}"
        f" of {len(df)}"
    )


if __name__ == "__main__":

    main()