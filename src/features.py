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


def drop_constant_features(X_train):
    """
    Drop columns with no variation in the training split.

    Computed on the training rows alone, never on the full
    frame, so that the test split cannot influence which
    features are considered.

    ambient_temperature is constant at 24 C across the
    four group-1 cells, so it currently carries no
    information. It is kept in FEATURES rather than deleted
    because the wider NASA dataset does vary between 4 C and
    44 C, and this guard will re-admit it automatically if
    those cells are ever included.
    """

    usable = [
        feature
        for feature in X_train.columns
        if X_train[feature].nunique(dropna=False) > 1
    ]

    dropped = [
        feature
        for feature in X_train.columns
        if feature not in usable
    ]

    return usable, dropped


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