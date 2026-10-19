"""Partial-window SOH estimator for the BMS backend.

Scores one discharge from the BMS-visible 4.0-3.4 V window plus the
cell's opening discharges. The model is the offline partial-window
SOH model (``models/soh_window_all``), trained on the cell-relative
window features and saved in the portable UBJ + JSON format.

The feature names, units and the ratio arithmetic are imported from
the offline pipeline (``src/features.py``); this module keeps no
private copy, so the estimator and the trainer cannot drift. A test
(``backend/tests/test_window_estimator.py``) fails if the two ever
disagree.

A cell is only scored once it has enough opening discharges to form a
baseline: fewer than ``WINDOW_MIN_REFERENCE_DISCHARGES`` raises
``InsufficientReferenceError`` rather than guessing. A discharge whose
cell-relative inputs leave the range the model was trained across
(widened by ``WINDOW_RANGE_SLACK``) is refused with
``OUT_OF_TRAINED_RANGE`` instead of a scored number, because the model
has no basis outside that range.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"

if str(_SRC) not in sys.path:

    sys.path.insert(0, str(_SRC))

from features import (  # noqa: E402
    OUT_OF_TRAINED_RANGE,
    WINDOW_FEATURES,
    WINDOW_MIN_REFERENCE_DISCHARGES,
    WINDOW_MODEL_FEATURES,
    WINDOW_REFERENCE_WINDOW,
    WINDOW_UNITS,
    WINDOW_VOLTAGE_HIGH,
    WINDOW_VOLTAGE_LOW,
    WindowRangeError,
    out_of_range_features,
    window_model_row,
)

# The model's input set. Aliased rather than re-declared so there is
# exactly one definition, in src/features.py.
MODEL_FEATURES = WINDOW_MODEL_FEATURES

# The portable booster the estimator loads when the caller does not
# name one. Kept under models/ so it travels with the repository.
DEFAULT_MODEL_PATH = _REPO_ROOT / "models" / "soh_window_all.ubj"

_BUNDLE_CACHE: dict[str, dict] = {}


class InsufficientReferenceError(ValueError):
    """Raised when a cell has too few opening discharges to baseline."""


def _as_rows(early_discharges) -> list:
    """Normalise a cell's opening discharges to a list of mappings."""

    if hasattr(early_discharges, "iterrows"):

        return [
            row.to_dict()
            for _, row in early_discharges.iterrows()
        ]

    return list(early_discharges)


def _load_model(model_path=None):
    """
    Load and cache the portable partial-window model.

    Returns the ``model`` object from the bundle (a
    ``predict.PortablePipeline``), which takes a DataFrame keyed by
    ``bundle['features']`` and returns a NumPy array of predictions.
    """

    path = (
        Path(model_path)
        if model_path is not None
        else DEFAULT_MODEL_PATH
    )

    key = str(path)

    if key in _BUNDLE_CACHE:

        return _BUNDLE_CACHE[key]

    from predict import load_portable_bundle

    bundle = load_portable_bundle(path)

    if bundle is None:

        raise FileNotFoundError(
            f"partial-window SOH model not found at {path}. "
            "Train one with:  python src/train_window_soh.py"
        )

    _BUNDLE_CACHE[key] = bundle

    return bundle


def predict_soh(
    window_features: Mapping[str, Any],
    early_discharges: Iterable[Mapping[str, Any]],
    *,
    model_path=None,
) -> float | str:
    """
    Estimate state of health (%) for one discharge.

    Parameters
    ----------
    window_features
        The discharge's raw 4.0-3.4 V window measurement. Must carry
        every name in ``WINDOW_FEATURES`` plus ``current_mean`` (see
        ``WINDOW_UNITS`` for units). The window's mean voltage must lie
        inside the trained 4.0-3.4 V band; a value outside it is
        refused rather than scored.
    early_discharges
        The cell's opening discharges, in order. At least
        ``WINDOW_MIN_REFERENCE_DISCHARGES`` are required; the baseline
        is the median of the first ``WINDOW_REFERENCE_WINDOW`` of them.
    model_path
        Optional override for the model to load.

    Returns
    -------
    float
        The estimated SOH, or the string ``"out of trained range"``
        if any cell-relative model input leaves the range the model
        was trained across (widened by ``WINDOW_RANGE_SLACK``). The
        model has no basis there — ElasticNet's blow-ups on a
        random-load cell are the reason — so the caller is told the
        input is out of range instead of handed a number.

    Raises
    ------
    InsufficientReferenceError
        If fewer than ``WINDOW_MIN_REFERENCE_DISCHARGES`` early
        discharges are supplied.
    WindowRangeError
        If ``vw_voltage_mean`` is present and outside the trained
        ``WINDOW_VOLTAGE_LOW`` to ``WINDOW_VOLTAGE_HIGH`` window.
    """

    rows = _as_rows(early_discharges)

    if len(rows) < WINDOW_MIN_REFERENCE_DISCHARGES:

        raise InsufficientReferenceError(
            "insufficient reference: need at least "
            f"{WINDOW_MIN_REFERENCE_DISCHARGES} early discharges, "
            f"got {len(rows)}"
        )

    inputs = window_model_row(window_features, rows)

    bundle = _load_model(model_path)

    if out_of_range_features(inputs, bundle.get("feature_ranges", {})):

        return OUT_OF_TRAINED_RANGE

    model = bundle["model"]

    import pandas as pd

    matrix = pd.DataFrame(
        [{name: inputs[name] for name in bundle["features"]}]
    )

    return float(model.predict(matrix)[0])
