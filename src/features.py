"""
features.py
------------
Extract engineered features from battery charge/discharge cycles.
"""


FEATURES = [

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

    # capacity_change_ah and soh_change_pct were both here.
    # They are first differences of capacity_ah and of the SOH
    # target itself, so they are target-derived by
    # construction and cannot be computed for a cell the model
    # has not already seen. Dropping them was measured to be
    # slightly *better*, not worse (mean LOCO MAE 3.2712
    # without versus 3.3514 with, at default hyperparameters),
    # so the removal cost nothing here. "Harmless in this
    # sample" is not the reason they were removed: the reason
    # is that they are not available at inference time.
    #
    # Note there is still no causal replacement for them.
    # A delta or slope feature computed over a trailing window
    # is the obvious candidate and is not implemented yet, so
    # the model currently has no explicit fade-rate input.
]

# Absolute cycle count is deliberately not a feature. It carried
# the train/serve skew (offline index counted every raw sequence
# entry, streaming counted discharges) and it lets the model
# memorise the training cells' calendars instead of reading the
# battery. Age is represented by causal trailing slopes instead.
# See causal.py: the mentor's load / last load / change in
# capacity / change in other parameters live there.
try:
    from causal import CAUSAL_FEATURES as _CAUSAL
except ImportError:  # imported as a package module
    from .causal import CAUSAL_FEATURES as _CAUSAL

FEATURES = FEATURES + [
    f for f in _CAUSAL if f not in FEATURES
]

# Columns that must never be used as predictive inputs,
# because they are derived from the target or from future
# cycles. This is the single canonical list: evaluation.py
# imports it rather than keeping its own copy, so the two
# guards cannot drift apart and silently weaken each other.
LEAKY_FEATURES = [
    # the target itself and its algebraic restatements
    "soh",
    "capacity_fade_pct",
    "soh_change_pct",

    # first difference of the measured capacity, which is the
    # numerator of the target
    "capacity_change_ah",

    # the measured capacity and the reference the target is
    # computed against
    "capacity_ah",
    "reference_capacity_ah",

    # remaining life and the end-of-life cycle it is measured
    # against
    "rul_cycles",
    "rul_cycles_80",
    "eol_cycle_threshold",
    "eol_cycle_observed",
    "is_pre_eol",

    # degradation slopes fitted over the full lifetime, which
    # already encode cycles after the one being predicted
    "soh_slope_pct_per_cycle",
    "soh_slope_early_pct_per_cycle",
]


def make_xy(df, target, leaky_columns=LEAKY_FEATURES):

    available_features = [
        feature
        for feature in FEATURES
        if feature in df.columns
    ]

    # Fail loudly rather than returning a matrix that trains
    # on the answer. This is the single choke point every
    # training script goes through, so a leaky column added
    # to FEATURES above cannot reach an estimator unnoticed.
    leaked = sorted(
        set(available_features) & set(leaky_columns)
    )

    if leaked:

        raise ValueError(
            "Target-derived columns are present in "
            f"FEATURES and would reach the estimator: {leaked}\n"
            f"Target is {target!r}. A feature that is a "
            "function of the target, of the cell's "
            "lifetime, or of future cycles cannot be "
            "observed for a cell the model has not seen.\n"
            "Remove it from FEATURES in src/features.py."
        )

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