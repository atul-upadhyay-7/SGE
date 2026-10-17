"""
window_robustness.py
--------------------
Do the partial-window SOH model and the window formula survive real
sensor noise and shorter voltage windows?

The partial-discharge model is trained on the nine constant-current
cells and scored cell by cell: leave-one-cell-out within G1 and within
G3, then train on every G1 and G3 cell and test on B0053. Every scored
cell is therefore held out of its own training set. On those held-out
cells only, this stage rebuilds the 4.0-3.4 V window features from the
raw traces after

  * adding Gaussian voltage noise at 5, 10 and 20 mV, and Gaussian
    current noise at 1, 2 and 5% (of each sample's current), and
  * narrowing the window to 3.9-3.5 V and 3.8-3.6 V.

The training features stay clean; only the held-out cell is perturbed,
so this measures inference-time robustness, not a change of what the
model was trained on. MAE is reported per level, and the smallest
window and largest noise at which the pooled held-out MAE stays under
3% are recorded. Outputs:

  data/processed/soh_window_robustness.csv          (per cell detail)
  data/processed/soh_window_robustness_summary.csv  (per condition)
  data/processed/soh_window_robustness_limits.csv   (the answers)
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
    WINDOW_VOLTAGE_HIGH,
    WINDOW_VOLTAGE_LOW,
)
from load_data import (  # noqa: E402
    build_cycle_features,
    iter_discharge_traces,
    resolve_raw_dir,
)
from repro import rounded  # noqa: E402
from run_experiments import (  # noqa: E402
    TARGET,
    cell_relative_features,
    window_formula,
)

PROC = ROOT / "data" / "processed"
CLEAN = PROC / "nasa_ml_dataset_clean.csv"

DETAIL_OUT = PROC / "soh_window_robustness.csv"
SUMMARY_OUT = PROC / "soh_window_robustness_summary.csv"
LIMITS_OUT = PROC / "soh_window_robustness_limits.csv"

# The failure budget this phase is read against: an MAE below it counts
# as "the estimator still works at this perturbation".
MAE_LIMIT_PCT = 3.0

# Deterministic per (cell, condition) noise. Seeding from a tuple means
# a rerun on another machine draws the same numbers, so the committed
# MAEs reproduce and verify.sh's docs-sync check passes.
SEED = 1729

VOLTAGE_NOISE_MV = (5.0, 10.0, 20.0)
CURRENT_NOISE_PCT = (1.0, 2.0, 5.0)

HIGH = WINDOW_VOLTAGE_HIGH
LOW = WINDOW_VOLTAGE_LOW

# Shorter windows, expressed as insets from the trained 4.0-3.4 V band,
# so they move with the model's own band rather than hard-coded again.
NARROW_BANDS = (
    ("window_3.9_3.5", round(HIGH - 0.1, 4), round(LOW + 0.1, 4)),
    ("window_3.8_3.6", round(HIGH - 0.2, 4), round(LOW + 0.2, 4)),
)


def conditions():
    """The nine tested operating points, clean baseline first."""

    out = [
        {
            "condition": "clean",
            "kind": "clean",
            "voltage_noise_mv": 0.0,
            "current_noise_pct": 0.0,
            "window_high_v": HIGH,
            "window_low_v": LOW,
        }
    ]

    for mv in VOLTAGE_NOISE_MV:

        out.append({
            "condition": f"voltage_noise_{int(mv)}mv",
            "kind": "voltage",
            "voltage_noise_mv": float(mv),
            "current_noise_pct": 0.0,
            "window_high_v": HIGH,
            "window_low_v": LOW,
        })

    for pct in CURRENT_NOISE_PCT:

        out.append({
            "condition": f"current_noise_{int(pct)}pct",
            "kind": "current",
            "voltage_noise_mv": 0.0,
            "current_noise_pct": float(pct),
            "window_high_v": HIGH,
            "window_low_v": LOW,
        })

    for name, high, low in NARROW_BANDS:

        out.append({
            "condition": name,
            "kind": "window",
            "voltage_noise_mv": 0.0,
            "current_noise_pct": 0.0,
            "window_high_v": high,
            "window_low_v": low,
        })

    for item in out:
        item["window_span_v"] = round(
            item["window_high_v"] - item["window_low_v"], 4
        )

    return out


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


def perturbed_cell_frame(traces, condition, seed):
    """
    Rebuild every discharge's window features from one cell's raw
    traces after applying `condition`'s noise and window band.

    The noise is drawn from a `numpy` generator seeded by `seed`, so the
    same cell and condition produce the same perturbation every run.
    """

    rng = np.random.default_rng(seed)

    voltage_sigma = condition["voltage_noise_mv"] / 1000.0
    current_sigma = condition["current_noise_pct"] / 100.0

    rows = []

    for trace in traces:

        voltage = np.asarray(trace["voltage"], dtype=float)
        current = np.asarray(trace["current"], dtype=float)

        if voltage_sigma:
            voltage = voltage + rng.normal(
                0.0, voltage_sigma, size=voltage.shape
            )

        if current_sigma:
            current = current * (
                1.0 + rng.normal(0.0, current_sigma, size=current.shape)
            )

        row = build_cycle_features(
            voltage=voltage,
            current=current,
            temperature=trace["temperature"],
            time=trace["time"],
            capacity=trace["capacity"],
            ambient_temperature=trace["ambient_temperature"],
            cell_id=trace["cell_id"],
            cycle_number=trace["cycle_number"],
            window_high=condition["window_high_v"],
            window_low=condition["window_low_v"],
        )

        if row is not None:
            rows.append(row)

    return pd.DataFrame(rows)


def held_out_frame(clean, features, cell_id):
    """
    Line `features` up with the clean dataset's valid discharges for one
    cell, carrying the target and the group/current metadata the
    cell-relative builder needs. Only discharges the production rule
    kept are scored, so a condition is compared on the same rows.
    """

    truth = clean[clean["cell_id"] == cell_id][
        ["cell_id", "cycle", "discharge_index", "group",
         "nominal_current_a", "soh"]
    ]

    aligned = features.merge(truth, on=["cell_id", "cycle"], how="inner")

    return aligned.sort_values("discharge_index").reset_index(drop=True)


def robustness_table(raw_dir=None):
    """
    Score every condition on every held-out cell and return
    (detail, summary, limits) frames.
    """

    raw_dir = Path(raw_dir) if raw_dir is not None else resolve_raw_dir()

    clean = pd.read_csv(CLEAN)
    clean_relative = cell_relative_features(clean)

    cells = sorted(clean["cell_id"].unique())
    group_of = {
        cell: clean.loc[clean["cell_id"] == cell, "group"].iloc[0]
        for cell in cells
    }

    # Read each .mat once; every condition perturbs these arrays.
    traces_by_cell = {
        cell: list(iter_discharge_traces(raw_dir / f"{cell}.mat"))
        for cell in cells
    }

    levels = conditions()

    rows = []

    for cell_index, cell in enumerate(cells):

        group = group_of[cell]

        if group in ("G1", "G3"):

            protocol = f"leave-one-cell-out: {group}"
            fold = clean_relative[
                (clean_relative["group"] == group)
                & (clean_relative["cell_id"] != cell)
            ]

        else:

            protocol = "train G1+G3, test B0053"
            fold = clean_relative[
                clean_relative["group"].isin(["G1", "G3"])
            ]

        for condition_index, condition in enumerate(levels):

            features = perturbed_cell_frame(
                traces_by_cell[cell],
                condition,
                (SEED, cell_index, condition_index),
            )

            aligned = held_out_frame(clean, features, cell)

            scored = window_formula(cell_relative_features(aligned))
            scored = scored.dropna(
                subset=list(WINDOW_MODEL_FEATURES)
            ).reset_index(drop=True)

            if scored.empty:
                continue

            base = {
                "condition": condition["condition"],
                "kind": condition["kind"],
                "voltage_noise_mv": condition["voltage_noise_mv"],
                "current_noise_pct": condition["current_noise_pct"],
                "window_high_v": condition["window_high_v"],
                "window_low_v": condition["window_low_v"],
                "window_span_v": condition["window_span_v"],
                "protocol": protocol,
                "test_cell": cell,
                "n_cycles": int(len(scored)),
            }

            for model_name in ("ElasticNet", "XGBoost"):

                model = build_model(model_name)
                model.fit(fold[WINDOW_MODEL_FEATURES], fold[TARGET])
                predicted = model.predict(scored[WINDOW_MODEL_FEATURES])

                rows.append({
                    **base,
                    "model": model_name,
                    "mae_pct": _mae(scored[TARGET], predicted),
                    "r2": _r2(scored[TARGET], predicted),
                })

            rows.append({
                **base,
                "model": "window_formula",
                "mae_pct": _mae(scored[TARGET], scored["_window_formula"]),
                "r2": _r2(scored[TARGET], scored["_window_formula"]),
            })

    detail = pd.DataFrame(rows)

    if detail.empty:
        raise ValueError("no robustness rows were produced")

    summary = _summarize(detail, levels)
    limits = _limits(summary)

    return detail, summary, limits


def _pooled(detail, condition, model):
    """Held-out MAE pooled over all nine cells, weighted by cycles."""

    block = detail[
        (detail["condition"] == condition) & (detail["model"] == model)
    ]

    if block.empty:
        return np.nan

    weights = block["n_cycles"].to_numpy(dtype=float)
    values = block["mae_pct"].to_numpy(dtype=float)

    return float(np.sum(values * weights) / np.sum(weights))


def _summarize(detail, levels):
    """One row per condition: pooled held-out MAE for each method."""

    rows = []

    for condition in levels:

        name = condition["condition"]
        block = detail[detail["condition"] == name]

        # Each condition is scored by three methods on the same rows;
        # count the held-out discharges once, not once per method.
        xgb_block = block[block["model"] == "XGBoost"]

        rows.append({
            **{k: condition[k] for k in (
                "condition", "kind", "voltage_noise_mv",
                "current_noise_pct", "window_high_v", "window_low_v",
                "window_span_v",
            )},
            "n_cycles": int(xgb_block["n_cycles"].sum()),
            "xgb_mae_pct": _pooled(detail, name, "XGBoost"),
            "elasticnet_mae_pct": _pooled(detail, name, "ElasticNet"),
            "window_formula_mae_pct": _pooled(
                detail, name, "window_formula"
            ),
            "under_limit": bool(
                _pooled(detail, name, "XGBoost") < MAE_LIMIT_PCT
            ),
        })

    return pd.DataFrame(rows)


def _limits(summary):
    """
    The smallest window and largest noise whose pooled held-out MAE is
    still under MAE_LIMIT_PCT. Blank cells mean nothing at that level
    survived.
    """

    window = summary[summary["kind"].isin(["clean", "window"])]
    window_ok = window[window["xgb_mae_pct"] < MAE_LIMIT_PCT]

    voltage = summary[summary["kind"] == "voltage"]
    voltage_ok = voltage[voltage["xgb_mae_pct"] < MAE_LIMIT_PCT]

    current = summary[summary["kind"] == "current"]
    current_ok = current[current["xgb_mae_pct"] < MAE_LIMIT_PCT]

    def _pick(frame, column, mode):
        if frame.empty:
            return None
        idx = frame[column].idxmin() if mode == "min" else frame[column].idxmax()
        return frame.loc[idx]

    smallest_window = _pick(window_ok, "window_span_v", "min")
    largest_voltage = _pick(voltage_ok, "voltage_noise_mv", "max")
    largest_current = _pick(current_ok, "current_noise_pct", "max")

    row = {
        "mae_limit_pct": MAE_LIMIT_PCT,
        "baseline_xgb_mae_pct": float(
            summary.loc[summary["condition"] == "clean", "xgb_mae_pct"].iloc[0]
        ),
        "smallest_window_span_v": (
            float(smallest_window["window_span_v"])
            if smallest_window is not None else np.nan
        ),
        "smallest_window_high_v": (
            float(smallest_window["window_high_v"])
            if smallest_window is not None else np.nan
        ),
        "smallest_window_low_v": (
            float(smallest_window["window_low_v"])
            if smallest_window is not None else np.nan
        ),
        "smallest_window_condition": (
            str(smallest_window["condition"])
            if smallest_window is not None else "none"
        ),
        "largest_voltage_noise_mv": (
            float(largest_voltage["voltage_noise_mv"])
            if largest_voltage is not None else np.nan
        ),
        "largest_voltage_condition": (
            str(largest_voltage["condition"])
            if largest_voltage is not None else "none"
        ),
        "largest_current_noise_pct": (
            float(largest_current["current_noise_pct"])
            if largest_current is not None else np.nan
        ),
        "largest_current_condition": (
            str(largest_current["condition"])
            if largest_current is not None else "none"
        ),
    }

    return pd.DataFrame([row])


def write_outputs(detail, summary, limits):

    rounded(detail).to_csv(DETAIL_OUT, index=False)
    rounded(summary).to_csv(SUMMARY_OUT, index=False)
    rounded(limits).to_csv(LIMITS_OUT, index=False)


def main():

    detail, summary, limits = robustness_table()

    write_outputs(detail, summary, limits)

    print(summary.to_string(index=False))
    print()
    print(limits.to_string(index=False))

    print(f"\nwrote {DETAIL_OUT.relative_to(ROOT)}")
    print(f"wrote {SUMMARY_OUT.relative_to(ROOT)}")
    print(f"wrote {LIMITS_OUT.relative_to(ROOT)}")


if __name__ == "__main__":

    main()
