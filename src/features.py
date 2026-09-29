"""
features.py
------------
Extract engineered features from battery charge/discharge cycles.
"""


FEATURES = [

    "cycle",

    "ambient_temperature",

    "voltage_mean",
    "voltage_min",
    "voltage_max",
    "voltage_std",
    "voltage_range",

    "current_mean",
    "current_min",
    "current_max",
    "current_std",

    "temperature_mean",
    "temperature_min",
    "temperature_max",
    "temperature_std",

    "discharge_duration_s",

    "voltage_start",
    "voltage_end",
    "voltage_drop",

    "resistance_proxy_ohm",

    "capacity_change_ah",
    "soh_change_pct"
]


def make_xy(df, target):

    available_features = [
        feature
        for feature in FEATURES
        if feature in df.columns
    ]

    X = df[available_features].copy()

    y = df[target].copy()

    return X, y, available_features


def split_by_cell(
    df,
    test_cell
):

    train = df[
        df["cell_id"] != test_cell
    ].copy()

    test = df[
        df["cell_id"] == test_cell
    ].copy()

    return train, test