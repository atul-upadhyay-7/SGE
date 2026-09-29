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


# Only these cells are used for modelling.
#
# The repository ships all 34 NASA cells, but the other 30
# are not usable for State-of-Health work:
#
#   - Groups G4/G6/G8/G9 contain mid-life capacity collapse
#     and recovery (B0042 drops to 0.09 Ah then returns to
#     1.48 Ah), which corrupts any capacity-derived target.
#   - Groups G2/G3 barely age within the test window
#     (B0025 fades 4% over 28 cycles), so they carry no
#     degradation signal.
#   - Groups G5/G6/G7 switch ambient temperature partway
#     through, so a single cell mixes 4 C and 24 C regimes.
#
# Group G1 (24 C, 2 A constant current) is the only group
# with a clean, full, monotonically decaying trajectory:
# 1.86 Ah down to 1.19-1.43 Ah over 132-168 discharge cycles.
CELLS = [
    "B0005",
    "B0006",
    "B0007",
    "B0018"
]


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