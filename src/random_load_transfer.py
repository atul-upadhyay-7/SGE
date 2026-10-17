"""
random_load_transfer.py
-----------------------
Does the deployable partial-window SOH model transfer to the
random-load cells it was never trained on?

The model that ships (models/soh_window_all) is trained on the nine
constant-current cells the audit keeps: the four G1 cells, the four
G3 cells and B0053. The random-load groups G5 and G6 are left out of
training because their load and temperature change across the record.
Four of those cells still reach the 80% threshold and pass the keep
filter (B0039, B0042, B0043, B0044), so they are a held-out condition
in the strictest sense: the model has never seen them and was not
tuned for their changing load.

This stage feeds each of them to that shipped model with exactly the
features the online estimator builds (features.window_model_row: the
4.0-3.4 V window made relative to the cell's own first-five-discharge
baseline, plus capacity per amp), and scores the result against the
measured SOH the production rule gives, with the window formula —
the physics baseline a BMS can compute from the window alone — on
exactly the same rows.

It adds no model and changes no training set; it answers whether the
exclusion of these cells is a transfer failure or only a comparability
choice. Output: data/processed/soh_random_load_transfer.csv
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from features import (  # noqa: E402
    WINDOW_MODEL_FEATURES,
    WINDOW_REFERENCE_WINDOW,
    window_model_row,
)
from load_data import resolve_raw_dir  # noqa: E402
from predict import load_portable_bundle  # noqa: E402
from random_load import clean_cell_frame  # noqa: E402
from repro import rounded  # noqa: E402

ROOT = HERE.parent
OUT = ROOT / "data" / "processed" / "soh_random_load_transfer.csv"

# The shipped partial-window model. Its trained_on list is asserted
# below to exclude every cell scored here, so this stage cannot
# silently become an in-sample check.
MODEL = ROOT / "models" / "soh_window_all.ubj"

# The random-load cells the audit keeps (they reach 80% and pass the
# keep filter), with the group they belong to.
TRANSFER_CELLS = {
    "B0039": "G5",
    "B0042": "G6",
    "B0043": "G6",
    "B0044": "G6",
}


def _mae(actual, predicted):
    return float(
        np.mean(
            np.abs(np.asarray(actual, float) - np.asarray(predicted, float))
        )
    )


def _r2(actual, predicted):
    actual = np.asarray(actual, float)
    predicted = np.asarray(predicted, float)
    ss_res = float(np.sum((actual - predicted) ** 2))
    ss_tot = float(np.sum((actual - actual.mean()) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan


def score_cell(bundle, raw_dir, cell_id, group):
    """
    One row: the shipped model and the window formula on one random-load
    cell, both scored against the production-rule SOH.
    """

    cell, _reference = clean_cell_frame(raw_dir, cell_id)

    if cell.empty:

        raise ValueError(
            f"{cell_id} has no valid discharge under the production rule"
        )

    early = [
        cell.loc[index].to_dict()
        for index in range(min(WINDOW_REFERENCE_WINDOW, len(cell)))
    ]

    built = pd.DataFrame(
        [window_model_row(cell.loc[index], early) for index in cell.index]
    )[list(bundle["features"])]

    predicted = bundle["model"].predict(built)
    truth = cell["soh"].to_numpy(dtype=float)

    # The window formula reads the same window: its amp-hours over the
    # cell's own first-five-discharge window median, as a percentage.
    window_reference = float(
        cell["vw_capacity_ah"]
        .dropna()
        .head(WINDOW_REFERENCE_WINDOW)
        .median()
    )
    formula = (
        cell["vw_capacity_ah"].to_numpy(dtype=float)
        / window_reference
        * 100.0
    )

    return {
        "cell_id": cell_id,
        "group": group,
        "n_cycles": int(len(cell)),
        "model_mae_pct": _mae(truth, predicted),
        "model_r2": _r2(truth, predicted),
        "window_formula_mae_pct": _mae(truth, formula),
        "window_formula_r2": _r2(truth, formula),
    }


def transfer_table(bundle=None, raw_dir=None):
    """The per-cell table, with a mean row, as a DataFrame."""

    if bundle is None:

        bundle = load_portable_bundle(MODEL)

    if bundle is None:

        raise FileNotFoundError(
            f"the shipped window model is missing: {MODEL}.ubj/.json"
        )

    if list(bundle["features"]) != list(WINDOW_MODEL_FEATURES):

        raise ValueError(
            "the shipped model's feature order is not "
            "features.WINDOW_MODEL_FEATURES; the transfer score would "
            "be built on the wrong inputs"
        )

    if set(TRANSFER_CELLS) & set(bundle["trained_on"]):

        raise ValueError(
            "the shipped model was trained on a random-load cell; this "
            "would be an in-sample check, not a transfer"
        )

    raw_dir = Path(raw_dir) if raw_dir is not None else resolve_raw_dir()

    rows = [
        score_cell(bundle, raw_dir, cell_id, group)
        for cell_id, group in sorted(TRANSFER_CELLS.items())
    ]

    frame = pd.DataFrame(rows)
    frame = frame.sort_values("cell_id").reset_index(drop=True)

    frame.loc[len(frame)] = {
        "cell_id": "MEAN",
        "group": "",
        "n_cycles": int(frame["n_cycles"].sum()),
        "model_mae_pct": float(frame["model_mae_pct"].mean()),
        "model_r2": float(frame["model_r2"].mean()),
        "window_formula_mae_pct": float(
            frame["window_formula_mae_pct"].mean()
        ),
        "window_formula_r2": float(frame["window_formula_r2"].mean()),
    }

    return frame


def main():

    frame = transfer_table()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    rounded(frame).to_csv(OUT, index=False)

    print(frame.to_string(index=False))

    mean = frame[frame["cell_id"] == "MEAN"].iloc[0]
    beaten = int(
        (
            frame.loc[frame["cell_id"] != "MEAN", "model_mae_pct"]
            < frame.loc[frame["cell_id"] != "MEAN", "window_formula_mae_pct"]
        ).sum()
    )
    print(
        f"\nmodel mean MAE {mean['model_mae_pct']:.4f}% versus window "
        f"formula {mean['window_formula_mae_pct']:.4f}%"
    )
    print(f"model beats the formula on {beaten} of 4 cells")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":

    main()
