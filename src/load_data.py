"""
load_data.py
------------
Load NASA Battery Dataset (.mat files) and convert to usable Python structures.
"""

from pathlib import Path
import numpy as np
import pandas as pd
from scipy.io import loadmat


RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")


def to_scalar(value):
    arr = np.asarray(value)

    if arr.size == 1:
        return arr.reshape(-1)[0].item()

    return value


def to_1d(value):
    arr = np.asarray(value, dtype=float)
    return arr.reshape(-1)


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

        if len(voltage) < 5:
            continue

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

    cells = [
        "B0005",
        "B0006",
        "B0007",
        "B0018"
    ]

    all_data = []

    for cell in cells:

        file_path = (
            RAW_DIR / f"{cell}.mat"
        )

        if not file_path.exists():

            raise FileNotFoundError(
                f"Missing file: {file_path}"
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