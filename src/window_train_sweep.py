"""
window_train_sweep.py
---------------------
Does a shorter voltage window work if the model is trained on it?

The window-robustness stage fed shorter bands to the *shipped* model,
which is trained on the 4.0-3.4 V feature distribution, and watched the
inputs move off what it learnt. That is a mismatch test, not a verdict
on shorter windows. This stage trains a separate model per window:

  * 4.0-3.4 V (the shipped band itself),
  * the two symmetric insets 3.9-3.5 V and 3.8-3.6 V,
  * a raised band, 4.1-3.7 V, and a lowered band, 3.7-3.4 V,

each with its own cell-relative features recomputed from the raw traces
over that band only. Every window is scored the same way the present
partial-discharge model is: leave-one-cell-out within G1 and within G3,
then train on every G1 and G3 cell and test on B0053 (G9) and on the
four random-load cells the audit keeps (G5/G6). Every scored cell is
held out of its own training set, and a window's training rows never
enter another window's model.

A window is judged on three held-out numbers at once, not on the pool
alone: the pooled MAE, the MAE on the random-load cells alone, and the
worst single held-out cell. It passes only when all three are under the
failure budget. XGBoost is the model; ElasticNet is carried only as a
comparison and is not part of the verdict.

Outputs:

  data/processed/soh_window_train_sweep.csv          (per cell detail)
  data/processed/soh_window_train_sweep_summary.csv  (per window)
  data/processed/soh_window_train_sweep_limits.csv   (the answer)
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ROOT = HERE.parent

from evaluation import build_model  # noqa: E402
from features import (  # noqa: E402
    WINDOW_MODEL_FEATURES,
    WINDOW_REFERENCE_WINDOW,
    WINDOW_VOLTAGE_HIGH,
    WINDOW_VOLTAGE_LOW,
    WindowRangeError,
    window_model_row,
)

# Raw window inputs the cell-relative features are ratios of. A
# discharge that never entered the band leaves these NaN; such a row
# carries no window measurement and must not be scored (nor set the
# baseline), or a band a cell does not reach would be graded on
# imputed values.
WINDOW_INPUTS = (
    "vw_capacity_ah",
    "vw_time_s",
    "vw_voltage_mean",
    "vw_dqdv_peak_height",
)
from load_data import (  # noqa: E402
    build_cycle_features,
    iter_discharge_traces,
    resolve_raw_dir,
)
from random_load import clean_cell_frame  # noqa: E402
from random_load_transfer import TRANSFER_CELLS  # noqa: E402
from repro import (  # noqa: E402
    MAE_UNSTABLE_ABOVE_PCT,
    R2_UNSTABLE_BELOW,
    UNSTABLE,
    rounded,
    stable_metric,
)

PROC = ROOT / "data" / "processed"
CLEAN = PROC / "nasa_ml_dataset_clean.csv"

DETAIL_OUT = PROC / "soh_window_train_sweep.csv"
SUMMARY_OUT = PROC / "soh_window_train_sweep_summary.csv"
LIMITS_OUT = PROC / "soh_window_train_sweep_limits.csv"

# The failure budget, the same one the robustness stage is read
# against. A window passes only when all three held-out numbers its
# verdict rests on are below it: the pooled MAE, the MAE on the
# random-load cells alone, and the worst single held-out cell. The pool
# alone can hide a band that loses one cell badly.
MAE_LIMIT_PCT = 3.0

# The shipped band is stated once in features.py; the other bands are
# literal voltages the phase asks about. Keeping the trained band
# derived means a change to features.py moves the row the model
# actually ships with.
TRAINED_HIGH = WINDOW_VOLTAGE_HIGH
TRAINED_LOW = WINDOW_VOLTAGE_LOW

BANDS = (
    ("window_4.0_3.4", TRAINED_HIGH, TRAINED_LOW),
    ("window_3.9_3.5", 3.9, 3.5),
    ("window_3.8_3.6", 3.8, 3.6),
    ("window_4.1_3.7", 4.1, 3.7),
    ("window_3.7_3.4", 3.7, 3.4),
)

CONSTANT_CURRENT_GROUPS = ("G1", "G3")
RANDOM_LOAD_CELLS = tuple(sorted(TRANSFER_CELLS))


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


def _truth_frames(clean, raw_dir, random_load_cells):
    """The production-rule target for every cell this stage scores."""

    frames = []

    for cell in sorted(clean["cell_id"].unique()):

        frames.append(
            clean.loc[
                clean["cell_id"] == cell,
                ["cell_id", "cycle", "group", "soh"],
            ]
        )

    for cell in random_load_cells:

        g_clean, _reference = clean_cell_frame(raw_dir, cell)

        if g_clean.empty:
            continue

        frame = g_clean[["cell_id", "cycle", "soh"]].copy()
        frame["group"] = TRANSFER_CELLS[cell]
        frames.append(frame)

    return pd.concat(frames, ignore_index=True)


def band_cell_frame(traces, cell_id, high, low):
    """
    One cell's window features over `high`-`low` V, plus the window
    formula value, for every discharge the extractor can characterise.

    The feature arithmetic is `features.window_model_row`, the same
    function the online estimator uses, so a trained-on-this-band model
    sees exactly the inputs it would see in the field.
    """

    rows = []

    for trace in traces:

        row = build_cycle_features(
            voltage=trace["voltage"],
            current=trace["current"],
            temperature=trace["temperature"],
            time=trace["time"],
            capacity=trace["capacity"],
            ambient_temperature=trace["ambient_temperature"],
            cell_id=trace["cell_id"],
            cycle_number=trace["cycle_number"],
            window_high=high,
            window_low=low,
        )

        if row is not None:
            rows.append(row)

    frame = pd.DataFrame(rows)

    if frame.empty:
        return frame

    # Keep only discharges that actually crossed the band.
    frame = frame.dropna(subset=list(WINDOW_INPUTS))

    if frame.empty:
        return frame

    frame = frame.sort_values("cycle").reset_index(drop=True)

    early = [
        frame.loc[index].to_dict()
        for index in range(min(WINDOW_REFERENCE_WINDOW, len(frame)))
    ]

    reference = float(
        frame["vw_capacity_ah"].dropna().head(WINDOW_REFERENCE_WINDOW).median()
    )

    records = []

    for index in frame.index:

        try:
            features = window_model_row(frame.loc[index], early)

        except (WindowRangeError, ValueError):
            continue

        records.append({
            "cell_id": cell_id,
            "cycle": int(frame.loc[index, "cycle"]),
            **features,
            "_window_formula": float(
                frame.loc[index, "vw_capacity_ah"] / reference * 100.0
            ),
        })

    return pd.DataFrame(records)


def band_frame(traces_by_cell, truth, high, low):
    """Every scored cell's band features, joined to the target."""

    frames = []

    for cell, traces in traces_by_cell.items():

        cell_frame = band_cell_frame(traces, cell, high, low)

        if not cell_frame.empty:
            frames.append(cell_frame)

    if not frames:
        return pd.DataFrame()

    features = pd.concat(frames, ignore_index=True)

    return features.merge(truth, on=["cell_id", "cycle"], how="inner")


def _score(rows, band, protocol, test, train):

    for model_name in ("ElasticNet", "XGBoost"):

        model = build_model(model_name)
        model.fit(train[list(WINDOW_MODEL_FEATURES)], train["soh"])
        predicted = model.predict(test[list(WINDOW_MODEL_FEATURES)])

        rows.append({
            **band,
            "protocol": protocol,
            "test_cell": str(test["cell_id"].iloc[0]),
            "test_group": str(test["group"].iloc[0]),
            "is_random_load": bool(test["cell_id"].iloc[0] in RANDOM_LOAD_CELLS),
            "model": model_name,
            "n_cycles": int(len(test)),
            "mae_pct": stable_metric(
                _mae(test["soh"], predicted), high=MAE_UNSTABLE_ABOVE_PCT
            ),
            "r2": stable_metric(
                _r2(test["soh"], predicted), low=R2_UNSTABLE_BELOW
            ),
        })

    rows.append({
        **band,
        "protocol": protocol,
        "test_cell": str(test["cell_id"].iloc[0]),
        "test_group": str(test["group"].iloc[0]),
        "is_random_load": bool(test["cell_id"].iloc[0] in RANDOM_LOAD_CELLS),
        "model": "window_formula",
        "n_cycles": int(len(test)),
        "mae_pct": stable_metric(
            _mae(test["soh"], test["_window_formula"]),
            high=MAE_UNSTABLE_ABOVE_PCT,
        ),
        "r2": stable_metric(
            _r2(test["soh"], test["_window_formula"]), low=R2_UNSTABLE_BELOW
        ),
    })


def sweep_table(raw_dir=None):

    raw_dir = Path(raw_dir) if raw_dir is not None else resolve_raw_dir()

    clean = pd.read_csv(CLEAN)

    constant_current = sorted(clean["cell_id"].unique())
    cells = constant_current + list(RANDOM_LOAD_CELLS)

    truth = _truth_frames(clean, raw_dir, RANDOM_LOAD_CELLS)

    traces_by_cell = {
        cell: list(iter_discharge_traces(raw_dir / f"{cell}.mat"))
        for cell in cells
    }

    rows = []

    for name, high, low in BANDS:

        band = {
            "window": name,
            "window_high_v": float(high),
            "window_low_v": float(low),
            "window_span_v": round(float(high) - float(low), 4),
        }

        data = band_frame(traces_by_cell, truth, high, low)

        for group in CONSTANT_CURRENT_GROUPS:

            g = data[data["group"] == group]

            for cell in sorted(g["cell_id"].unique()):

                _score(
                    rows, band, f"leave-one-cell-out: {group}",
                    g[g["cell_id"] == cell],
                    g[g["cell_id"] != cell],
                )

        source = data[data["group"].isin(CONSTANT_CURRENT_GROUPS)]

        for cell in ["B0053"] + list(RANDOM_LOAD_CELLS):

            test = data[data["cell_id"] == cell]

            if test.empty:
                continue

            _score(
                rows, band,
                "train G1+G3, test B0053"
                if cell == "B0053" else "train G1+G3, test random-load",
                test, source,
            )

    detail = pd.DataFrame(rows)

    if detail.empty:
        raise ValueError("no window train-sweep rows were produced")

    summary = _summarize(detail)
    limits = _limits(summary)

    return detail, summary, limits


def _pooled(detail, window, model, subset):
    """Cycle-weighted MAE over a subset of the held-out cells.

    If any contributing cell's MAE is UNSTABLE the pooled value is
    UNSTABLE too: averaging a number that is not a measurement would
    invent one. Rounding to the written precision keeps the pooled
    float stable across machines the same way the per-cell rows are.
    """

    block = detail[(detail["window"] == window) & (detail["model"] == model)]

    if subset == "constant_current":
        block = block[~block["is_random_load"]]
    elif subset == "random_load":
        block = block[block["is_random_load"]]

    if block.empty:
        return np.nan

    values = block["mae_pct"]

    if (values == UNSTABLE).any():
        return UNSTABLE

    weights = block["n_cycles"].to_numpy(dtype=float)
    numbers = values.astype(float).to_numpy(dtype=float)

    pooled = float(np.sum(numbers * weights) / np.sum(weights))

    return stable_metric(pooled, high=MAE_UNSTABLE_ABOVE_PCT)


def _worst_cell(detail, window, model):
    """The single held-out cell with the largest MAE for this window.

    The third number a window is judged on: a band can hold the pooled
    and random-load means and still lose one cell badly. If any cell's
    MAE is UNSTABLE the worst is UNSTABLE too -- a band that produced a
    non-measurement has not earned a pass.
    """

    block = detail[(detail["window"] == window) & (detail["model"] == model)]

    if block.empty:
        return np.nan

    values = block["mae_pct"]

    if (values == UNSTABLE).any():
        return UNSTABLE

    return stable_metric(
        float(values.astype(float).max()), high=MAE_UNSTABLE_ABOVE_PCT
    )


def _under(value):
    """True only for a real, finite metric that sits below the budget."""

    return bool(isinstance(value, float) and value < MAE_LIMIT_PCT)


def _summarize(detail):

    rows = []

    for name, high, low in BANDS:

        subset = detail[detail["window"] == name]
        xgb = subset[subset["model"] == "XGBoost"]

        pooled = _pooled(detail, name, "XGBoost", "all")
        random_load = _pooled(detail, name, "XGBoost", "random_load")
        worst = _worst_cell(detail, name, "XGBoost")

        rows.append({
            "window": name,
            "window_high_v": float(high),
            "window_low_v": float(low),
            "window_span_v": round(float(high) - float(low), 4),
            "n_cells": int(xgb["test_cell"].nunique()),
            "n_cycles": int(xgb["n_cycles"].sum()),
            "xgb_mae_pct": _pooled(detail, name, "XGBoost", "constant_current"),
            "elasticnet_mae_pct": _pooled(
                detail, name, "ElasticNet", "constant_current"
            ),
            "window_formula_mae_pct": _pooled(
                detail, name, "window_formula", "constant_current"
            ),
            "xgb_mae_random_load_pct": random_load,
            "elasticnet_mae_random_load_pct": _pooled(
                detail, name, "ElasticNet", "random_load"
            ),
            "xgb_mae_all_pct": pooled,
            "elasticnet_mae_all_pct": _pooled(
                detail, name, "ElasticNet", "all"
            ),
            "xgb_mae_worst_cell_pct": worst,
            "passes_pooled": _under(pooled),
            "passes_random_load": _under(random_load),
            "passes_worst_cell": _under(worst),
            "under_limit": (
                _under(pooled) and _under(random_load) and _under(worst)
            ),
        })

    return pd.DataFrame(rows)


def _limits(summary):
    """
    The smallest window that holds all three held-out numbers -- pooled
    MAE, random-load-only MAE and the worst single cell -- under
    MAE_LIMIT_PCT. `none` in the name means no attempted window survived
    all three; the smallest window tried is reported either way.
    """

    ok = summary[summary["under_limit"]]
    smallest_ok = (
        ok.loc[ok["window_span_v"].idxmin()] if not ok.empty else None
    )
    smallest_tried = summary.loc[summary["window_span_v"].idxmin()]

    trained = summary[summary["window"] == "window_4.0_3.4"].iloc[0]

    def _value(frame, column):
        return float(frame[column]) if frame is not None else np.nan

    return pd.DataFrame([{
        "mae_limit_pct": MAE_LIMIT_PCT,
        "n_windows": int(len(summary)),
        "n_under_limit": int(len(ok)),
        "trained_window": str(trained["window"]),
        "trained_window_mae_pct": float(trained["xgb_mae_all_pct"]),
        "trained_window_random_load_mae_pct": float(
            trained["xgb_mae_random_load_pct"]
        ),
        "trained_window_worst_cell_mae_pct": float(
            trained["xgb_mae_worst_cell_pct"]
        ),
        "smallest_under_limit_window": (
            str(smallest_ok["window"]) if smallest_ok is not None else "none"
        ),
        "smallest_under_limit_span_v": (
            _value(smallest_ok, "window_span_v")
            if smallest_ok is not None else np.nan
        ),
        "smallest_under_limit_high_v": (
            _value(smallest_ok, "window_high_v")
            if smallest_ok is not None else np.nan
        ),
        "smallest_under_limit_low_v": (
            _value(smallest_ok, "window_low_v")
            if smallest_ok is not None else np.nan
        ),
        "smallest_under_limit_mae_pct": (
            _value(smallest_ok, "xgb_mae_all_pct")
            if smallest_ok is not None else np.nan
        ),
        "smallest_under_limit_random_load_mae_pct": (
            _value(smallest_ok, "xgb_mae_random_load_pct")
            if smallest_ok is not None else np.nan
        ),
        "smallest_under_limit_worst_cell_mae_pct": (
            _value(smallest_ok, "xgb_mae_worst_cell_pct")
            if smallest_ok is not None else np.nan
        ),
        "smallest_tried_window": str(smallest_tried["window"]),
        "smallest_tried_span_v": float(smallest_tried["window_span_v"]),
        "smallest_tried_mae_pct": float(smallest_tried["xgb_mae_all_pct"]),
        "smallest_tried_random_load_mae_pct": float(
            smallest_tried["xgb_mae_random_load_pct"]
        ),
        "smallest_tried_worst_cell_mae_pct": float(
            smallest_tried["xgb_mae_worst_cell_pct"]
        ),
    }])


def write_outputs(detail, summary, limits):

    rounded(detail).to_csv(DETAIL_OUT, index=False)
    rounded(summary).to_csv(SUMMARY_OUT, index=False)
    rounded(limits).to_csv(LIMITS_OUT, index=False)


def main():

    detail, summary, limits = sweep_table()

    write_outputs(detail, summary, limits)

    print(summary.to_string(index=False))
    print()
    print(limits.to_string(index=False))

    print(f"\nwrote {DETAIL_OUT.relative_to(ROOT)}")
    print(f"wrote {SUMMARY_OUT.relative_to(ROOT)}")
    print(f"wrote {LIMITS_OUT.relative_to(ROOT)}")


if __name__ == "__main__":

    main()
