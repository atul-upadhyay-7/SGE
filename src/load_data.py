"""
load_data.py
------------
Load NASA Battery Dataset (.mat files) and convert to usable Python structures.
"""

from pathlib import Path
import numpy as np
import pandas as pd
from scipy.io import loadmat


PROCESSED_DIR = Path("data/processed")

# Directories searched, in order, for the NASA .mat files.
# data/raw is the documented drop location and is gitignored.
# The BMS Dataset folder ships a copy in the repository,
# so the pipeline stays runnable without a manual download.
RAW_DIR_CANDIDATES = [
    Path("data/raw"),
    Path("BMS Dataset/Battery_DataSet/Battery_DataSet")
]


def resolve_raw_dir():
    """
    Return the first candidate directory that holds .mat files.
    """

    for candidate in RAW_DIR_CANDIDATES:

        if candidate.is_dir() and any(
            candidate.glob("*.mat")
        ):

            return candidate

    searched = "\n  ".join(
        str(path) for path in RAW_DIR_CANDIDATES
    )

    raise FileNotFoundError(
        "No .mat files found in any of:\n"
        f"  {searched}\n\n"
        "Download the NASA Battery Dataset and place the "
        ".mat files in data/raw/."
    )


def discover_cells(raw_dir):
    """
    Return the sorted cell ids present in a directory.
    """

    return sorted(
        path.stem.upper()
        for path in raw_dir.glob("*.mat")
    )


# ── Cell groups ─────────────────────────────────────────────
#
# Constant-current (CC) groups suitable for SOH modelling.
# Other groups are excluded for specific reasons:
#   G2  – square-wave load (resistance_proxy_ohm is invalid)
#   G5  – mixed loads, temperature switches mid-life
#   G6  – fixed 1A & 4A alternating load, 4°C ambient
#   G8  – test rig crashed; data is incomplete
#
# Every group here discharges at a fixed current level, so
# the features (especially resistance_proxy_ohm) are
# consistent across cycles within a cell.
GROUPS = {
    "G1": {
        "cells": ["B0005", "B0006", "B0007", "B0018"],
        "current_a": 2.0,
        "temperature_c": 24,
        "cutoff_v": [2.7, 2.5, 2.2, 2.5],
    },
    "G3": {
        "cells": ["B0029", "B0030", "B0031", "B0032"],
        "current_a": 4.0,
        "temperature_c": 43,
        "cutoff_v": [2.0, 2.2, 2.5, 2.7],
    },
    "G4": {
        "cells": ["B0033", "B0034", "B0036"],
        "current_a": 4.0,
        "temperature_c": 24,
        "cutoff_v": [2.0, 2.2, 2.7],
    },
    "G7": {
        "cells": ["B0045", "B0046", "B0047", "B0048"],
        "current_a": 1.0,
        "temperature_c": 4,
        "cutoff_v": [2.0, 2.2, 2.5, 2.7],
    },
    "G9": {
        "cells": ["B0053", "B0054", "B0055", "B0056"],
        "current_a": 2.0,
        "temperature_c": 4,
        "cutoff_v": [2.0, 2.2, 2.5, 2.7],
    },
}

# Flat list of all cells across all CC groups.
CELLS = [
    cell
    for group in GROUPS.values()
    for cell in group["cells"]
]

# Lookup: cell_id → group name
CELL_TO_GROUP = {
    cell: group_name
    for group_name, info in GROUPS.items()
    for cell in info["cells"]
}

# Backward-compatible alias for code that only expects G1.
CELLS_G1 = GROUPS["G1"]["cells"]

# Default retirement threshold for SOH-based EOL.
RETIREMENT_THRESHOLD_PCT = 80


def to_scalar(value):
    arr = np.asarray(value)

    if arr.size == 1:
        return arr.reshape(-1)[0].item()

    return value


def to_1d(value):
    arr = np.asarray(value, dtype=float)
    return arr.reshape(-1)


# A cycle needs at least this many valid samples before its
# statistics describe the cycle rather than measurement noise.
MIN_CYCLE_SAMPLES = 5

# Rated capacity of the 18650 cells in the NASA dataset.
# Used to compute soh_rated = capacity_ah / RATED_CAPACITY_AH × 100,
# which does not require the cell's own first cycle as a reference.
RATED_CAPACITY_AH = 2.0

# Voltage window shared by all cells. Every discharge crosses
# this range, regardless of cutoff voltage (2.2–2.7 V across
# cells). Features extracted from this window describe the
# cell's state without requiring the full discharge.
VOLTAGE_WINDOW_HIGH = 4.0
VOLTAGE_WINDOW_LOW = 3.4


def _voltage_window_features(voltage, current, time):
    """
    Extract features from the voltage window [HIGH → LOW].

    Returns a dict of window features, or a dict of NaNs if the
    cycle does not fully traverse the window.
    """

    nan_result = {
        "vw_time_s": np.nan,
        "vw_capacity_ah": np.nan,
        "vw_voltage_mean": np.nan,
        "vw_dqdv_peak_v": np.nan,
        "vw_dqdv_peak_height": np.nan,
    }

    # Find the first sample at or below HIGH
    below_high = np.where(voltage <= VOLTAGE_WINDOW_HIGH)[0]
    if len(below_high) == 0:
        return nan_result

    # Find the first sample at or below LOW after entering the window
    start_idx = below_high[0]
    below_low = np.where(
        voltage[start_idx:] <= VOLTAGE_WINDOW_LOW
    )[0]

    if len(below_low) == 0:
        return nan_result

    end_idx = start_idx + below_low[0]

    if end_idx <= start_idx + 2:
        return nan_result

    # Slice to the window
    v_win = voltage[start_idx:end_idx + 1]
    i_win = current[start_idx:end_idx + 1]
    t_win = time[start_idx:end_idx + 1]

    window_time = t_win[-1] - t_win[0]

    # Capacity in the window: integrate |current| × dt
    dt = np.diff(t_win)
    i_mid = (np.abs(i_win[:-1]) + np.abs(i_win[1:])) / 2
    window_capacity = float(np.sum(i_mid * dt) / 3600.0)

    window_v_mean = float(v_win.mean())

    # dQ/dV: capacity derivative with respect to voltage.
    # The peak position and height encode the phase transition
    # voltage and its sharpness, both of which shift with ageing.
    dv = np.diff(v_win)
    dq = i_mid * dt / 3600.0  # Ah per step

    # Avoid division by zero in flat-voltage regions
    nonzero = np.abs(dv) > 1e-6
    if nonzero.sum() < 3:
        peak_v = np.nan
        peak_height = np.nan
    else:
        dqdv = np.abs(dq[nonzero] / dv[nonzero])
        v_mid = (v_win[:-1] + v_win[1:]) / 2
        v_mid = v_mid[nonzero]
        peak_idx = np.argmax(dqdv)
        peak_v = float(v_mid[peak_idx])
        peak_height = float(dqdv[peak_idx])

    return {
        "vw_time_s": float(window_time),
        "vw_capacity_ah": window_capacity,
        "vw_voltage_mean": window_v_mean,
        "vw_dqdv_peak_v": peak_v,
        "vw_dqdv_peak_height": peak_height,
    }


def build_cycle_features(
    voltage,
    current,
    temperature,
    time,
    capacity=np.nan,
    ambient_temperature=np.nan,
    cell_id="UNKNOWN",
    cycle_number=0
):
    """
    Build the per-cycle feature row from one cycle's
    measured samples.

    Shared by the offline .mat loader and the streaming
    cycle tracker so that a cycle produces the same features
    whether it arrived as a file or one sample per second.

    Returns None when the cycle holds too few valid samples
    to characterise, which is not an error: a partial cycle
    at the end of a stream is expected to be too short.
    """

    voltage = np.asarray(
        voltage,
        dtype=float
    )

    current = np.asarray(
        current,
        dtype=float
    )

    temperature = np.asarray(
        temperature,
        dtype=float
    )

    time = np.asarray(
        time,
        dtype=float
    )

    # Remove invalid values
    mask = (
        np.isfinite(voltage)
        & np.isfinite(current)
        & np.isfinite(temperature)
        & np.isfinite(time)
    )

    voltage = voltage[mask]
    current = current[mask]
    temperature = temperature[mask]
    time = time[mask]

    if len(voltage) < MIN_CYCLE_SAMPLES:
        return None

    discharge_duration = (
        time[-1] - time[0]
    )

    voltage_range = (
        voltage.max() - voltage.min()
    )

    current_range = (
        current.max() - current.min()
    )

    if abs(current_range) > 1e-8:

        resistance_proxy = (
            voltage_range / current_range
        )

    else:

        resistance_proxy = np.nan

    # Voltage-window features (partial-discharge features)
    vw = _voltage_window_features(voltage, current, time)

    row = {

        "cell_id": cell_id,

        "cycle": cycle_number,

        "ambient_temperature":
            ambient_temperature,

        "capacity_ah":
            capacity,

        "voltage_mean":
            voltage.mean(),

        "voltage_min":
            voltage.min(),

        "voltage_max":
            voltage.max(),

        "voltage_std":
            voltage.std(),

        "voltage_range":
            voltage_range,

        "current_mean":
            current.mean(),

        "current_min":
            current.min(),

        "current_max":
            current.max(),

        "current_std":
            current.std(),

        "temperature_mean":
            temperature.mean(),

        "temperature_min":
            temperature.min(),

        "temperature_max":
            temperature.max(),

        "temperature_std":
            temperature.std(),

        "discharge_duration_s":
            discharge_duration,

        "voltage_start":
            voltage[0],

        "voltage_end":
            voltage[-1],

        "voltage_drop":
            voltage[0] - voltage[-1],

        "resistance_proxy_ohm":
            resistance_proxy
    }

    row.update(vw)

    return row


def parse_battery_file(mat_path):

    mat_path = Path(mat_path)

    cell_id = mat_path.stem.upper()

    print(f"\nLoading {cell_id}...")

    mat = loadmat(
        mat_path,
        squeeze_me=True,
        struct_as_record=False
    )

    print("MAT variables:", mat.keys())

    if cell_id in mat:
        battery = mat[cell_id]
    else:

        candidates = [
            key for key in mat.keys()
            if not key.startswith("__")
        ]

        if not candidates:
            raise ValueError(
                f"No battery variable found in {mat_path}"
            )

        battery = mat[candidates[0]]

    cycles = getattr(battery, "cycle", None)

    if cycles is None:
        raise ValueError(
            f"Could not find 'cycle' in {mat_path}"
        )

    cycles = np.atleast_1d(cycles)

    rows = []

    for cycle_number, cycle in enumerate(cycles, start=1):

        cycle_type = str(
            to_scalar(
                getattr(cycle, "type", "")
            )
        ).lower()

        # We initially use only discharge cycles
        if "discharge" not in cycle_type:
            continue

        data = getattr(cycle, "data", None)

        if data is None:
            continue

        try:

            voltage = to_1d(
                getattr(data, "Voltage_measured")
            )

            current = to_1d(
                getattr(data, "Current_measured")
            )

            temperature = to_1d(
                getattr(data, "Temperature_measured")
            )

            time = to_1d(
                getattr(data, "Time")
            )

        except AttributeError as error:

            print(
                f"Skipping cycle {cycle_number}: {error}"
            )

            continue

        capacity_raw = getattr(
            data,
            "Capacity",
            np.nan
        )

        capacity_array = np.asarray(
            capacity_raw,
            dtype=float
        ).reshape(-1)

        if len(capacity_array) > 0:
            capacity = float(
                capacity_array[0]
            )
        else:
            capacity = np.nan

        ambient_raw = getattr(
            cycle,
            "ambient_temperature",
            np.nan
        )

        ambient_array = np.asarray(
            ambient_raw,
            dtype=float
        ).reshape(-1)

        if len(ambient_array) > 0:
            ambient_temperature = float(
                ambient_array[0]
            )
        else:
            ambient_temperature = np.nan

        # Remove invalid values
        row = build_cycle_features(
            voltage=voltage,
            current=current,
            temperature=temperature,
            time=time,
            capacity=capacity,
            ambient_temperature=ambient_temperature,
            cell_id=cell_id,
            cycle_number=cycle_number
        )

        if row is None:
            continue

        rows.append(row)

    df = pd.DataFrame(rows)

    if df.empty:

        raise ValueError(
            f"No discharge cycles extracted from {cell_id}"
        )

    df = df.sort_values(
        "cycle"
    ).reset_index(drop=True)

    return df


def parse_all_cells():

    raw_dir = resolve_raw_dir()

    print("Reading .mat files from:")
    print(raw_dir)

    cells = discover_cells(
        raw_dir
    )

    available = set(cells)

    missing = [
        cell for cell in CELLS
        if cell not in available
    ]

    if missing:

        raise FileNotFoundError(
            "These required cells are not present in "
            f"{raw_dir}:\n  "
            f"{', '.join(missing)}\n\n"
            "Found instead:\n  "
            f"{', '.join(cells) if cells else '(no .mat files)'}"
        )

    all_data = []

    for cell in CELLS:

        file_path = (
            raw_dir / f"{cell}.mat"
        )

        df = parse_battery_file(
            file_path
        )

        print(
            f"{cell}: {len(df)} discharge cycles"
        )

        all_data.append(df)

    final_df = pd.concat(
        all_data,
        ignore_index=True
    )

    final_df = final_df.sort_values(
        ["cell_id", "cycle"]
    )

    PROCESSED_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    output = (
        PROCESSED_DIR /
        "nasa_cycle_features_raw.csv"
    )

    final_df.to_csv(
        output,
        index=False
    )

    print("\nSaved:")
    print(output)

    print("\nDataset shape:")
    print(final_df.shape)

    print("\nCycles per cell:")

    print(
        final_df.groupby("cell_id")
        ["cycle"]
        .agg(["min", "max", "count"])
    )


if __name__ == "__main__":

    parse_all_cells()