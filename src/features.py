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

    # Voltage-window features: extracted from the 4.0 → 3.4 V
    # window that every cell traverses regardless of cutoff.
    # These work from a partial discharge with no first-cycle
    # reference, making them the basis for the "predict SOH
    # from a partial discharge" task.
    "vw_time_s",
    "vw_capacity_ah",
    "vw_voltage_mean",
    "vw_dqdv_peak_v",
    "vw_dqdv_peak_height",

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
    "soh_rated",
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


# ---------------------------------------------------------------------
# Partial-window ("BMS-visible") feature contract
# ---------------------------------------------------------------------
#
# A discharge's first stage crosses 4.0 -> 3.4 V on every cell,
# regardless of where the run is later cut off. A BMS therefore sees
# that window even when it never sees the full discharge. These lists
# are the single canonical definition of what the partial-window SOH
# model consumes: the offline experiment code and the backend
# estimator (backend/app/ml/estimator.py) both import them from here,
# so neither keeps a private copy that could drift.

# Raw measurements available inside the 4.0-3.4 V window.
WINDOW_FEATURES = [
    "vw_time_s",
    "vw_capacity_ah",
    "vw_voltage_mean",
    "vw_dqdv_peak_v",
    "vw_dqdv_peak_height",
]

# Unit for every raw window input, plus the one non-window input the
# cell-relative normalisation needs. Carried so a caller can present
# and validate measurements without guessing.
WINDOW_UNITS = {
    "vw_time_s": "seconds",
    "vw_capacity_ah": "ampere_hours",
    "vw_voltage_mean": "volts",
    "vw_dqdv_peak_v": "volts",
    "vw_dqdv_peak_height": "ampere_hours_per_volt",
    "current_mean": "amperes",
}

# The model input set: each raw window quantity made relative to the
# cell's own opening-discharge baseline, or already scale-free
# (capacity per amp). No full-discharge quantity (duration,
# resistance, end voltage) enters, because a BMS that sees only the
# window never measures it.
WINDOW_MODEL_FEATURES = [
    "vw_capacity_ratio",
    "vw_capacity_per_amp",
    "vw_time_ratio",
    "vw_voltage_ratio",
    "vw_dqdv_height_ratio",
]

# Raw inputs the normalisation needs that are not themselves window
# quantities: current_mean scales window capacity to amp-hours per
# amp, removing the 2 A (G1/G9) versus 4 A (G3) rate difference.
WINDOW_NORMALIZATION_INPUTS = ["current_mean"]

# The discharge-rate band the partial-window model was trained across.
# The kept constant-current cells run at 2 A (G1, G9) and 4 A (G3);
# the per-amp normalisation above removes that rate difference, but
# only inside this band. A BMS that discharges far outside it is
# presenting a rate the model has no training data for, so the band is
# stated once here and quoted by the README rather than typed by hand.
WINDOW_TRAIN_CURRENT_LOW_A = 2.0
WINDOW_TRAIN_CURRENT_HIGH_A = 4.0

# The voltage band every WINDOW_FEATURES value is measured over. The
# extractor in load_data.py aliases these (VOLTAGE_WINDOW_HIGH/LOW),
# so the band the model was trained on has one definition here.
WINDOW_VOLTAGE_HIGH = 4.0
WINDOW_VOLTAGE_LOW = 3.4

# Sensor-resolution slack when checking a measurement against the
# band, so a value sitting on the boundary is not rejected.
WINDOW_VOLTAGE_TOL = 1e-3


class WindowRangeError(ValueError):
    """Raised when a window measurement is not inside the trained band."""


# How far past the training min/max a cell-relative model input may
# sit before the estimator refuses it: the allowed interval is the
# training [min, max] widened by this fraction of the training span on
# each side. ElasticNet's blow-ups showed that inputs outside the
# training range produce implausible SOH while the tree model stays
# bounded; the guard makes the estimator return a refusal instead of a
# number the model has no basis for.
WINDOW_RANGE_SLACK = 0.10

# The value the estimator returns (rather than a scored number) when a
# model input leaves the widened training range. Kept as one string so
# the estimator and its callers cannot disagree about the spelling.
OUT_OF_TRAINED_RANGE = "out of trained range"


def widen_trained_range(low, high, slack=WINDOW_RANGE_SLACK):
    """
    The allowed interval for one model input: the training ``[low,
    high]`` widened by ``slack`` of its span on each side.
    """

    span = float(high) - float(low)

    pad = slack * span

    return float(low) - pad, float(high) + pad


def out_of_range_features(inputs, ranges, slack=WINDOW_RANGE_SLACK):
    """
    The model inputs that sit outside their widened training range.

    `inputs` maps a model feature name to its value; `ranges` maps the
    same names to their training ``(min, max)``. Returns
    ``{name: (value, low, high)}`` for every input below ``low`` or
    above ``high`` after widening, so a caller can refuse to score
    rather than feed the model a value it was never trained on. A
    missing or NaN input is left to the imputer and is not flagged.
    """

    import numpy as np

    offenders = {}

    for name, bounds in ranges.items():

        if name not in inputs:
            continue

        value = inputs[name]

        if value is None:
            continue

        value = float(value)

        if np.isnan(value):
            continue

        low, high = widen_trained_range(bounds[0], bounds[1], slack)

        if value < low or value > high:

            offenders[name] = (value, low, high)

    return offenders


def validate_window_voltage(window_row):
    """
    Refuse a window measurement taken outside the 4.0-3.4 V band.

    Every WINDOW_FEATURES value is defined between WINDOW_VOLTAGE_HIGH
    and WINDOW_VOLTAGE_LOW, so a window whose mean voltage sits outside
    that band was measured over a different span. The model has no
    training data there and must not score it — silently imputing or
    extrapolating would produce a plausible-looking number from an
    input the model has never seen.

    The mean is checked because it is bounded by construction: the
    window runs from 4.0 to 3.4 V, so its mean is strictly inside.
    A missing (NaN) voltage is left to the imputer; only a present,
    out-of-band value is refused.
    """

    import numpy as np

    if hasattr(window_row, "to_dict"):

        row = window_row.to_dict()

    else:

        row = dict(window_row)

    mean = row.get("vw_voltage_mean")

    if mean is None:

        return

    mean = float(mean)

    if np.isnan(mean):

        return

    if not (
        WINDOW_VOLTAGE_LOW - WINDOW_VOLTAGE_TOL
        <= mean
        <= WINDOW_VOLTAGE_HIGH + WINDOW_VOLTAGE_TOL
    ):

        raise WindowRangeError(
            "partial-window model input is outside the trained "
            f"{WINDOW_VOLTAGE_HIGH:.1f}-{WINDOW_VOLTAGE_LOW:.1f} V window: "
            f"vw_voltage_mean={mean:.4f} V. The model was trained only on "
            "the window's mean voltage inside that band; a window measured "
            "over a different span has no training data and is refused."
        )

# Baseline = median of the cell's first `WINDOW_REFERENCE_WINDOW`
# valid discharges of each quantity.
WINDOW_REFERENCE_WINDOW = 5

# Refuse to score a cell with fewer opening discharges than this; the
# baseline would be too thin to trust and the estimate would be a
# guess.
WINDOW_MIN_REFERENCE_DISCHARGES = 3


def window_model_row(
    window_row,
    early_discharges,
    reference_window=WINDOW_REFERENCE_WINDOW,
):
    """
    Build the partial-window model input for one discharge.

    `window_row` is one discharge's raw window measurement and must
    carry every name in WINDOW_FEATURES plus WINDOW_NORMALIZATION_INPUTS.
    `early_discharges` is the cell's opening discharges, in order; the
    baseline is the median of the first `reference_window` of them.

    Returns the WINDOW_MODEL_FEATURES mapping the partial-window SOH
    model consumes. This is the same arithmetic
    run_experiments.cell_relative_features applies, factored out so
    the offline trainer and the online estimator cannot disagree about
    what a window feature is.
    """

    import numpy as np

    def as_mapping(source):

        if hasattr(source, "to_dict"):

            return source.to_dict()

        return dict(source)

    row = as_mapping(window_row)

    missing = [
        name for name in WINDOW_FEATURES + WINDOW_NORMALIZATION_INPUTS
        if name not in row
    ]

    if missing:

        raise KeyError(
            "window_row is missing required inputs: "
            f"{missing}"
        )

    # Refuse a measurement taken outside the trained voltage band
    # before any ratio arithmetic, so the caller gets a clear error
    # naming the window rather than a silently imputed prediction.
    validate_window_voltage(row)

    early = [
        as_mapping(discharge) for discharge in early_discharges
    ]

    if len(early) < WINDOW_MIN_REFERENCE_DISCHARGES:

        raise ValueError(
            "insufficient reference: need at least "
            f"{WINDOW_MIN_REFERENCE_DISCHARGES} early discharges, "
            f"got {len(early)}"
        )

    def baseline(column):

        values = [
            discharge[column] for discharge in early
            if discharge.get(column) is not None
        ]
        values = np.asarray(values, dtype=float)
        values = values[~np.isnan(values)]

        head = values[:reference_window]
        reference = float(np.median(head)) if head.size else np.nan

        # A zero or missing baseline would divide by zero. Fall back
        # to the median of every opening discharge, then to 1.0, so
        # the ratio stays finite. This mirrors the offline
        # fallback in cell_relative_features.
        if reference == 0.0 or np.isnan(reference):

            reference = (
                float(np.median(values)) if values.size else np.nan
            )

        if reference == 0.0 or np.isnan(reference):

            reference = 1.0

        return reference

    capacity = float(row["vw_capacity_ah"])
    current = float(row["current_mean"])

    return {
        "vw_capacity_ratio": capacity / baseline("vw_capacity_ah"),
        "vw_capacity_per_amp": capacity / abs(current),
        "vw_time_ratio": (
            float(row["vw_time_s"]) / baseline("vw_time_s")
        ),
        "vw_voltage_ratio": (
            float(row["vw_voltage_mean"]) / baseline("vw_voltage_mean")
        ),
        "vw_dqdv_height_ratio": (
            float(row["vw_dqdv_peak_height"])
            / baseline("vw_dqdv_peak_height")
        ),
    }


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