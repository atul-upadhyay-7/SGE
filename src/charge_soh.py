"""
charge_soh.py
-------------
Can the constant-current charge estimate state of health?

The shipped partial-window SOH model reads the 4.0-3.4 V discharge
window. The loader that feeds it keeps discharge cycles only:
`load_data.iter_discharge_traces` skips every cycle whose type does not
contain "discharge", so charge segments never reach any feature, model
or document. This stage is the separate reader that keeps them. It does
not touch the discharge loader, the ML dataset or any existing output.

From each cell's constant-current charge segments it extracts the same
window quantities the discharge model uses — time, capacity, mean
voltage and the dQ/dV peak — over the 3.8 -> 4.0 V band of the CC
charge (the phase is well inside CC: the cell charges at its fixed rate
until it reaches the upper cutoff, and the 3.8-4.0 V slice sits before
the constant-voltage tail). The upper bound is 4.0 V rather than 4.1 V
because some charges on the cold G9 cell B0053 top out near 4.08 V, so
a 4.1 V band is not crossed on every cycle; 3.8-4.0 V is. Each charge
segment is paired with the discharge that follows it, which carries the
production-rule SOH target, and the cell-relative ratios are rebuilt
with the same `features.window_model_row` arithmetic the discharge
model uses.

The charge model is scored on the same held-out protocol as the
discharge model so the two are comparable:

  * leave-one-cell-out within G1 and within G3;
  * train on every G1 and G3 cell and test B0053 (G9);
  * train on every G1 and G3 cell and test the four random-load cells.

It writes the charge-window scores and a side-by-side comparison with
the discharge-window model (the 4.0-3.4 V rows of
data/processed/soh_window_train_sweep.csv). Outputs:

  data/processed/soh_charge_window.csv             (per cell detail)
  data/processed/soh_charge_window_comparison.csv  (charge vs discharge)
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.io import loadmat

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ROOT = HERE.parent

from evaluation import build_model  # noqa: E402
from features import (  # noqa: E402
    WINDOW_MODEL_FEATURES,
    WINDOW_REFERENCE_WINDOW,
    WindowRangeError,
    window_model_row,
)
from load_data import (  # noqa: E402
    resolve_raw_dir,
    to_1d,
    to_scalar,
)
from random_load import clean_cell_frame  # noqa: E402
from random_load_transfer import TRANSFER_CELLS  # noqa: E402
from repro import (  # noqa: E402
    MAE_UNSTABLE_ABOVE_PCT,
    R2_UNSTABLE_BELOW,
    REPRO_DECIMALS,
    UNSTABLE,
    rounded,
    stable_metric,
)

PROC = ROOT / "data" / "processed"
CLEAN = PROC / "nasa_ml_dataset_clean.csv"
DISCHARGE_SWEEP = PROC / "soh_window_train_sweep.csv"

DETAIL_OUT = PROC / "soh_charge_window.csv"
COMPARISON_OUT = PROC / "soh_charge_window_comparison.csv"

# The constant-current charge band: a BMS can watch the voltage rise
# from 3.8 V to 4.0 V while the charger is still in CC. The upper bound
# is 4.0 V rather than the 4.1 V one might first try because the cold
# G9 cell B0053 (and B0032, B0039) top out at ~4.08 V on some charges,
# so a 4.1 V band is not crossed every cycle and would drop those cells
# from the held-out protocol. Every kept cell crosses 3.8-4.0 V.
CHARGE_WINDOW_HIGH = 4.0
CHARGE_WINDOW_LOW = 3.8

# The discharge band the charge model is compared against; stated here
# as the row to read from the discharge sweep rather than re-derived.
DISCHARGE_WINDOW = "window_4.0_3.4"

CONSTANT_CURRENT_GROUPS = ("G1", "G3")
RANDOM_LOAD_CELLS = tuple(sorted(TRANSFER_CELLS))

MAE_LIMIT_PCT = 3.0


def iter_charge_traces(mat_path):
    """
    Yield one dict per charge cycle in a .mat file.

    The same traversal as `load_data.iter_discharge_traces`, keeping
    charge cycles instead of discharges, so the discharge loader and
    its outputs are untouched. Keys match the discharge reader, which
    is what lets the same window arithmetic be reused.
    """

    mat_path = Path(mat_path)
    cell_id = mat_path.stem.upper()

    mat = loadmat(mat_path, squeeze_me=True, struct_as_record=False)

    if cell_id in mat:
        battery = mat[cell_id]
    else:
        candidates = [k for k in mat.keys() if not k.startswith("__")]
        if not candidates:
            raise ValueError(f"No battery variable found in {mat_path}")
        battery = mat[candidates[0]]

    cycles = getattr(battery, "cycle", None)

    if cycles is None:
        raise ValueError(f"Could not find 'cycle' in {mat_path}")

    cycles = np.atleast_1d(cycles)

    for cycle_number, cycle in enumerate(cycles, start=1):

        cycle_type = str(to_scalar(getattr(cycle, "type", ""))).lower()

        # "discharge" contains "charge", so match charge segments
        # exactly rather than by substring.
        if "discharge" in cycle_type or "charge" not in cycle_type:
            continue

        data = getattr(cycle, "data", None)

        if data is None:
            continue

        try:
            voltage = to_1d(getattr(data, "Voltage_measured"))
            current = to_1d(getattr(data, "Current_measured"))
            temperature = to_1d(getattr(data, "Temperature_measured"))
            time = to_1d(getattr(data, "Time"))
        except AttributeError:
            continue

        ambient_raw = getattr(cycle, "ambient_temperature", np.nan)
        ambient_array = np.asarray(ambient_raw, dtype=float).reshape(-1)
        ambient_temperature = (
            float(ambient_array[0]) if len(ambient_array) else np.nan
        )

        yield {
            "cell_id": cell_id,
            "cycle_number": cycle_number,
            "voltage": voltage,
            "current": current,
            "temperature": temperature,
            "time": time,
            "ambient_temperature": ambient_temperature,
        }


def charge_window_features(
    voltage,
    current,
    time,
    window_high=CHARGE_WINDOW_HIGH,
    window_low=CHARGE_WINDOW_LOW,
):
    """
    The window quantities over a *rising* band, [LOW -> HIGH].

    `load_data._voltage_window_features` walks the band downward for a
    discharge; a charge walks it upward, so the entry and exit tests are
    reversed. Everything else — the |I| dt capacity, the mean voltage
    and the dQ/dV peak — is the same arithmetic, so the two window
    vectors are directly comparable. Returns NaNs if the cycle does not
    traverse the whole band.
    """

    nan_result = {
        "vw_time_s": np.nan,
        "vw_capacity_ah": np.nan,
        "vw_voltage_mean": np.nan,
        "vw_dqdv_peak_v": np.nan,
        "vw_dqdv_peak_height": np.nan,
    }

    voltage = np.asarray(voltage, dtype=float)
    current = np.asarray(current, dtype=float)
    time = np.asarray(time, dtype=float)

    above_low = np.where(voltage >= window_low)[0]

    if len(above_low) == 0:
        return nan_result

    start_idx = above_low[0]
    above_high = np.where(voltage[start_idx:] >= window_high)[0]

    if len(above_high) == 0:
        return nan_result

    end_idx = start_idx + above_high[0]

    if end_idx <= start_idx + 2:
        return nan_result

    v_win = voltage[start_idx:end_idx + 1]
    i_win = current[start_idx:end_idx + 1]
    t_win = time[start_idx:end_idx + 1]

    window_time = t_win[-1] - t_win[0]

    dt = np.diff(t_win)
    i_mid = (np.abs(i_win[:-1]) + np.abs(i_win[1:])) / 2
    window_capacity = float(np.sum(i_mid * dt) / 3600.0)

    dv = np.diff(v_win)
    dq = i_mid * dt / 3600.0

    nonzero = np.abs(dv) > 1e-6

    if nonzero.sum() < 3:
        peak_v = np.nan
        peak_height = np.nan
    else:
        dqdv = np.abs(dq[nonzero] / dv[nonzero])
        v_mid = ((v_win[:-1] + v_win[1:]) / 2)[nonzero]
        peak_idx = np.argmax(dqdv)
        peak_v = float(v_mid[peak_idx])
        peak_height = float(dqdv[peak_idx])

    return {
        "vw_time_s": float(window_time),
        "vw_capacity_ah": window_capacity,
        "vw_voltage_mean": float(v_win.mean()),
        "vw_dqdv_peak_v": peak_v,
        "vw_dqdv_peak_height": peak_height,
        # The charge rate of this segment, used by the cell-relative
        # capacity-per-amp feature. Taken over the window, which is
        # inside the CC plateau, rather than the whole cycle.
        "current_mean": float(np.mean(np.abs(i_win))),
    }


def _next_discharge_map(mat_path):
    """
    charge cycle_number -> the cycle_number of the discharge that
    follows it in the file. Read from the same cycle list the readers
    walk, so the pairing cannot drift from the loaders.
    """

    mat = loadmat(Path(mat_path), squeeze_me=True, struct_as_record=False)
    cell_id = Path(mat_path).stem.upper()

    battery = mat[cell_id] if cell_id in mat else None

    if battery is None:
        candidates = [k for k in mat.keys() if not k.startswith("__")]
        battery = mat[candidates[0]]

    cycles = np.atleast_1d(battery.cycle)

    types = [
        str(to_scalar(getattr(c, "type", ""))).lower() for c in cycles
    ]

    mapping = {}

    for index, kind in enumerate(types):

        if "discharge" in kind or "charge" not in kind:
            continue

        for follow in range(index + 1, len(types)):

            if "discharge" in types[follow]:
                mapping[index + 1] = follow + 1
                break

    return mapping


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


def _truth_frames(clean, raw_dir):
    """The production-rule target per cell, keyed on discharge cycle."""

    frames = []

    for cell in sorted(clean["cell_id"].unique()):
        frames.append(
            clean.loc[
                clean["cell_id"] == cell, ["cell_id", "cycle", "group", "soh"]
            ]
        )

    for cell in RANDOM_LOAD_CELLS:

        g_clean, _reference = clean_cell_frame(raw_dir, cell)

        if g_clean.empty:
            continue

        frame = g_clean[["cell_id", "cycle", "soh"]].copy()
        frame["group"] = TRANSFER_CELLS[cell]
        frames.append(frame)

    return pd.concat(frames, ignore_index=True)


def charge_frame(raw_dir, clean):
    """
    Every scored cell's charge rows, joined to the SOH of the discharge
    each charge is paired with, with the cell-relative ratio features
    and the charge-window formula value.
    """

    truth = _truth_frames(clean, raw_dir)

    frames = []

    for cell in sorted(set(clean["cell_id"]) | set(RANDOM_LOAD_CELLS)):

        mat_path = raw_dir / f"{cell}.mat"

        traces = list(iter_charge_traces(mat_path))
        pairing = _next_discharge_map(mat_path)

        rows = []

        for trace in traces:

            row = charge_window_features(
                trace["voltage"], trace["current"], trace["time"]
            )

            if not np.isfinite(row["vw_capacity_ah"]):
                continue

            discharge_cycle = pairing.get(trace["cycle_number"])

            if discharge_cycle is None:
                continue

            rows.append({
                "cell_id": cell,
                "charge_cycle": int(trace["cycle_number"]),
                "discharge_cycle": int(discharge_cycle),
                **row,
            })

        cell_frame = pd.DataFrame(rows)

        if not cell_frame.empty:
            frames.append(cell_frame)

    if not frames:
        return pd.DataFrame()

    features = pd.concat(frames, ignore_index=True)

    features = features.merge(
        truth.rename(columns={"cycle": "discharge_cycle"}),
        on=["cell_id", "discharge_cycle"],
        how="inner",
    ).sort_values(["cell_id", "charge_cycle"]).reset_index(drop=True)

    # Cell-relative ratios with the same arithmetic the discharge model
    # uses, over the cell's own first charges.
    records = []

    for cell, g in features.groupby("cell_id", sort=False):

        g = g.reset_index(drop=True)
        early = [
            g.loc[index].to_dict()
            for index in range(min(WINDOW_REFERENCE_WINDOW, len(g)))
        ]
        reference = float(
            g["vw_capacity_ah"].dropna().head(WINDOW_REFERENCE_WINDOW).median()
        )

        for index in g.index:

            try:
                model_features = window_model_row(g.loc[index], early)
            except (WindowRangeError, ValueError):
                continue

            records.append({
                **g.loc[index].to_dict(),
                **model_features,
                "_window_formula": float(
                    g.loc[index, "vw_capacity_ah"] / reference * 100.0
                ),
            })

    return pd.DataFrame(records)


def _score(rows, protocol, test, train):

    for model_name in ("ElasticNet", "XGBoost"):

        model = build_model(model_name)
        model.fit(train[list(WINDOW_MODEL_FEATURES)], train["soh"])
        predicted = model.predict(test[list(WINDOW_MODEL_FEATURES)])

        rows.append({
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


def charge_table(raw_dir=None):

    raw_dir = Path(raw_dir) if raw_dir is not None else resolve_raw_dir()

    clean = pd.read_csv(CLEAN)

    data = charge_frame(raw_dir, clean)

    if data.empty:
        raise ValueError("no charge-window rows were produced")

    rows = []

    for group in CONSTANT_CURRENT_GROUPS:

        g = data[data["group"] == group]

        for cell in sorted(g["cell_id"].unique()):

            _score(
                rows, f"leave-one-cell-out: {group}",
                g[g["cell_id"] == cell],
                g[g["cell_id"] != cell],
            )

    source = data[data["group"].isin(CONSTANT_CURRENT_GROUPS)]

    for cell in ["B0053"] + list(RANDOM_LOAD_CELLS):

        test = data[data["cell_id"] == cell]

        if test.empty:
            continue

        _score(
            rows,
            "train G1+G3, test B0053"
            if cell == "B0053" else "train G1+G3, test random-load",
            test, source,
        )

    return data, pd.DataFrame(rows)


def _pooled(detail, protocol, model):
    """Cycle-weighted MAE for one protocol and model, UNSTABLE if any
    contributing cell's MAE was flagged."""

    block = detail[
        (detail["protocol"] == protocol) & (detail["model"] == model)
    ]
    if block.empty:
        return np.nan

    values = block["mae_pct"]

    if (values == UNSTABLE).any():
        return UNSTABLE

    weights = block["n_cycles"].to_numpy(dtype=float)
    numbers = values.astype(float).to_numpy(dtype=float)

    return stable_metric(
        float(np.sum(numbers * weights) / np.sum(weights)),
        high=MAE_UNSTABLE_ABOVE_PCT,
    )


def comparison_table(charge_detail, discharge_detail):

    protocols = [
        "leave-one-cell-out: G1",
        "leave-one-cell-out: G3",
        "train G1+G3, test B0053",
        "train G1+G3, test random-load",
    ]

    discharge = discharge_detail[
        discharge_detail["window"] == DISCHARGE_WINDOW
    ]

    rows = []

    for protocol in protocols:

        for model in ("XGBoost", "ElasticNet"):

            charge_block = charge_detail[
                (charge_detail["protocol"] == protocol)
                & (charge_detail["model"] == model)
            ]
            discharge_block = discharge[
                (discharge["protocol"] == protocol)
                & (discharge["model"] == model)
            ]

            if charge_block.empty or discharge_block.empty:
                continue

            rows.append({
                "protocol": protocol,
                "model": model,
                "n_cycles": int(charge_block["n_cycles"].sum()),
                "charge_mae_pct": _pooled(charge_detail, protocol, model),
                "discharge_mae_pct": _pooled(discharge, protocol, model),
            })

    comparison = pd.DataFrame(rows)

    if not comparison.empty:

        def _delta(row):
            charge, discharge = row["charge_mae_pct"], row["discharge_mae_pct"]
            if charge == UNSTABLE or discharge == UNSTABLE:
                return UNSTABLE
            return round(float(charge) - float(discharge), REPRO_DECIMALS)

        comparison["delta_mae_pct"] = comparison.apply(_delta, axis=1)

    return comparison


def write_outputs(detail, comparison):

    rounded(detail).to_csv(DETAIL_OUT, index=False)
    rounded(comparison).to_csv(COMPARISON_OUT, index=False)


def main():

    data, detail = charge_table()

    detail = detail.sort_values(
        ["protocol", "test_cell", "model"]
    ).reset_index(drop=True)

    discharge_detail = pd.read_csv(DISCHARGE_SWEEP)
    comparison = comparison_table(detail, discharge_detail)

    write_outputs(detail, comparison)

    print(comparison.to_string(index=False))
    print(f"\nwrote {DETAIL_OUT.relative_to(ROOT)}")
    print(f"wrote {COMPARISON_OUT.relative_to(ROOT)}")


if __name__ == "__main__":

    main()
