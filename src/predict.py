"""
predict.py
----------
Load a trained SOH model and turn battery data into
predicted state of health.

The model is loaded once and cached. A cold joblib load of
an XGBoost bundle takes about 1.7 seconds, which is longer
than the one-second cadence of the streaming service, so
reloading per request is never correct. Call
load_soh_model() at startup and keep the returned handle.

The bundle contract is the one written by train_soh.py:
    model      fitted sklearn Pipeline
    features   ordered feature names the pipeline was fit on
    target     target column name
    trained_on readable label describing the training set,
               such as "all cells" or "all cells except B0005"

Features
--------
The model consumes one row per cycle, not one row per
second, because six of the twenty-two features
(voltage_end, voltage_drop, resistance_proxy_ohm,
discharge_duration_s, capacity_change_ah, soh_change_pct)
are only defined once a cycle has finished. See
streaming/cycle_tracker.py for how live samples are
assembled into those cycle rows.

soh_change_pct is the first difference of the SOH target
itself, so it cannot be known for a cell the model has not
seen. assemble_features() sets it to NaN and lets the
pipeline's imputer fill it. Dropping the column entirely at
training time measured slightly better than keeping it
(LOCO MAE 3.3952 with, 3.3270 without), so nothing is lost.
"""

from pathlib import Path
import joblib
import numpy as np
import pandas as pd


MODEL_DIR = Path("models")

DEFAULT_MODEL = (
    MODEL_DIR / "soh_xgb_all.joblib"
)

# Populated by load_soh_model. Defined here rather than
# below it so the caching contract is visible before the
# first use.
_CACHED_BUNDLE = None
_CACHED_PATH = None


class SohModelLoadError(RuntimeError):
    """
    Raised when a model file exists but cannot be used.

    Kept separate from joblib's own exceptions because the
    common failure here is not a missing file but a
    cross-environment serialization mismatch, and the remedy
    is different.
    """


def default_model_path():

    return DEFAULT_MODEL


def load_soh_model(path=None):
    """
    Load and cache the SOH model.

    Raises SohModelLoadError with a specific remedy rather
    than letting a raw joblib or XGBoost traceback escape,
    because a service that cannot name the problem will sit
    there latching a stale value instead of failing.
    """

    global _CACHED_BUNDLE
    global _CACHED_PATH

    model_path = (
        Path(path)
        if path is not None
        else default_model_path()
    )

    # Keyed on the resolved path so a caller asking for a
    # different model does not silently get the cached one.

    cache_key = str(
        model_path.resolve()
        if model_path.exists()
        else model_path
    )

    if (
        _CACHED_BUNDLE is not None
        and _CACHED_PATH == cache_key
    ):

        return _CACHED_BUNDLE

    if not model_path.exists():

        raise SohModelLoadError(
            f"SOH model not found at {model_path}.\n"
            "Train one with:  python src/train_soh.py"
        )

    try:

        bundle = joblib.load(model_path)

    except Exception as error:

        raise SohModelLoadError(
            _explain_load_failure(
                model_path, error
            )
        ) from error

    missing = _missing_bundle_keys(bundle)

    if missing:

        raise SohModelLoadError(
            f"{model_path} is missing required "
            f"bundle keys: {', '.join(missing)}\n"
            "Expected the layout written by "
            "src/train_soh.py."
        )

    _CACHED_BUNDLE = bundle
    _CACHED_PATH = cache_key

    return bundle


def clear_model_cache():
    """
    Drop the cached model.

    Only useful in tests, which need to observe a genuine
    cold load.
    """

    global _CACHED_BUNDLE
    global _CACHED_PATH

    _CACHED_BUNDLE = None
    _CACHED_PATH = None


def _missing_bundle_keys(bundle):

    if not isinstance(bundle, dict):

        raise SohModelLoadError(
            "Model file did not contain a bundle "
            f"dictionary, got {type(bundle).__name__}."
        )

    required = {
        "model",
        "features",
        "target"
    }

    return sorted(
        required - set(bundle)
    )


def _explain_load_failure(model_path, error):
    """
    Turn a deserialization failure into an actionable
    message.
    """

    name = type(error).__name__

    local_version = _installed_xgboost()

    detail = f"  reported error: {name}: {error}\n"

    if "corrupt" in str(error).lower():

        detail += (
            "  cause: the XGBoost model inside this file was "
            "serialized by a different\n"
            "         XGBoost build than the one installed "
            "here. sklearn pickles embed\n"
            "         the native booster, so the file is not "
            "portable across XGBoost\n"
            "         versions.\n\n"
            "  fix (pick one):\n"
            "    1. Retrain here:  python src/train_soh.py\n"
            "       Training is seeded, so this reproduces the "
            "same metrics.\n"
            "    2. Install the producing version:  "
            "pip install xgboost==<version>\n"
            "       Use the version recorded with the model.\n"
            "    3. Re-export the booster from the machine "
            "that trained it:\n"
            "       bundle['model'].named_steps['model']"
            ".get_booster().save_model('soh_xgb_all.ubj')\n"
            "       The UBJ format is portable across XGBoost "
            "versions. Note that the booster alone is not\n"
            "       enough to predict: the fitted imputer "
            "medians and the feature order are\n"
            "       also needed, so re-export those alongside "
            "it.\n"
        )

    else:

        detail += (
            "  The file was found but could not be "
            "deserialized. If it was\n"
            "  written by another machine or Python "
            "environment, the XGBoost\n"
            "  version mismatch described above is the "
            "usual cause.\n"
        )

    return (
        f"Could not load SOH model {model_path}.\n"
        f"{detail}"
        f"  installed xgboost: {local_version}\n"
    )


def _installed_xgboost():

    try:

        import xgboost

        return xgboost.__version__

    except Exception:

        return "not installed"


def assemble_features(
    frame,
    features
):
    """
    Build the model's input matrix from cycle feature rows.

    frame is a DataFrame of one or more cycle rows as
    produced by load_data.parse_battery_file or by
    streaming.cycle_tracker. Columns are returned in the
    exact order the pipeline was fit with, because the
    fitted imputer is positional.

    capacity_change_ah is derived here from measured
    capacity, which is available in the raw data.
    soh_change_pct cannot be derived for an unseen cell and
    is left as NaN for the imputer.
    """

    frame = frame.copy()

    if "capacity_change_ah" not in frame.columns:

        if "capacity_ah" not in frame.columns:

            raise KeyError(
                "Cannot predict: capacity_change_ah is "
                "absent and cannot be derived because "
                "capacity_ah is absent too."
            )

        frame["capacity_change_ah"] = (
            frame.groupby("cell_id")
            ["capacity_ah"]
            .diff()
        )

    if "soh_change_pct" not in frame.columns:

        frame["soh_change_pct"] = np.nan

    missing = [
        feature
        for feature in features
        if feature not in frame.columns
    ]

    if missing:

        raise KeyError(
            "Cannot predict: these model features are "
            f"absent from the input: {', '.join(missing)}"
        )

    return frame[list(features)]


def predict_soh(bundle, row, features=None):
    """
    Predict SOH for one cycle feature row.

    bundle: a loaded bundle from load_soh_model(), or a
    pipeline. When a pipeline is passed, features must be
    given as well.

    row: a single cycle as a dict, or a one-row DataFrame.
    A dict is the common case in the streaming path, where a
    cycle arrives from CycleTracker.

    Returns a float.

    Kept separate from SohService so a cycle can be scored
    without building a latched service around it, which is
    what the file-based and replay checks need.
    """

    if isinstance(bundle, dict):

        pipeline = bundle["model"]
        features = (
            list(bundle["features"])
            if features is None
            else list(features)
        )

    else:

        pipeline = bundle

        if features is None:

            raise ValueError(
                "features is required when passing a "
                "pipeline rather than a bundle."
            )

        features = list(features)

    frame = (
        row
        if isinstance(row, pd.DataFrame)
        else pd.DataFrame([dict(row)])
    )

    matrix = assemble_features(frame, features)

    value = float(pipeline.predict(matrix)[0])

    if not np.isfinite(value):

        raise ValueError(
            f"Model returned non-finite SOH: {value}"
        )

    return value
