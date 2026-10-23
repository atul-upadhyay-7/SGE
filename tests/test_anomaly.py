"""
test_anomaly.py
---------------
Offline anomaly detector guarantees.

The detectors in src/anomaly.py are presented as early
warning: a flag at cycle t must be the flag an online monitor
could have raised at cycle t. That means every threshold is
computed from earlier cycles of the same cell only. These
tests pin that causality (a later shock must not change any
earlier flag), the cleaned-dataset contract, and the
opening-baseline fallback in run_experiments, which must not
consult the cell's future either.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "src")
)

import anomaly  # noqa: E402
import features  # noqa: E402
import run_experiments  # noqa: E402
from pack import evaluate as pack_evaluate  # noqa: E402


def test_anomaly_reads_the_cleaned_dataset():
    """Every downstream stage reads the cleaned file
    (data_cleaning.py's contract); anomaly.py included."""

    assert anomaly.DATA_FILE.name == "nasa_ml_dataset_clean.csv"
    assert anomaly.DATA_FILE.exists()


def test_preregistered_constants_drop_by_name_only():

    usable, dropped = features.drop_preregistered_constants(
        ["ambient_temperature", "voltage_mean", "cycle_count"]
    )

    assert usable == ["voltage_mean", "cycle_count"]
    assert dropped == ["ambient_temperature"]


def _residual_frame(residuals):
    residuals = np.asarray(residuals, dtype=float)
    return pd.DataFrame({
        "cycle": np.arange(1, len(residuals) + 1),
        "soh": 90.0 + residuals,
        "predicted_soh": np.full(len(residuals), 90.0),
    })


def test_residual_flags_need_history_and_catch_a_shock():

    frame = _residual_frame([0.0] * 30 + [-5.0] + [0.0] * 5)

    out = anomaly.flag_residuals(frame)

    # No judging before CAUSAL_MIN_HISTORY earlier residuals.
    assert not out["residual_anomaly"].iloc[
        : anomaly.CAUSAL_MIN_HISTORY
    ].any()
    # A 5-point departure against a zero-spread history fires.
    assert bool(out["residual_anomaly"].iloc[30])
    assert (
        out["residual_direction"].iloc[30]
        == "faster_than_expected"
    )


def test_residual_flags_do_not_depend_on_the_future():

    base = [0.0] * 30 + [-5.0] + [0.0] * 9
    shocked = base + [50.0, -50.0] * 10

    prefix_flags = anomaly.flag_residuals(
        _residual_frame(base)
    )["residual_anomaly"].to_numpy()
    full_flags = anomaly.flag_residuals(
        _residual_frame(shocked)
    )["residual_anomaly"].to_numpy()[: len(base)]

    assert (prefix_flags == full_flags).all()


def _soh_frame(soh):
    soh = np.asarray(soh, dtype=float)
    return pd.DataFrame({
        "cycle": np.arange(1, len(soh) + 1),
        "soh": soh,
    })


def test_knee_flags_do_not_depend_on_the_future():

    gentle = 100.0 - 0.05 * np.arange(60)
    plunging = np.concatenate(
        [gentle, gentle[-1] - 2.0 * np.arange(1, 21)]
    )

    prefix = anomaly.flag_knee(_soh_frame(gentle))[
        "knee_anomaly"
    ].to_numpy()
    full = anomaly.flag_knee(_soh_frame(plunging))[
        "knee_anomaly"
    ].to_numpy()[: len(gentle)]

    assert (prefix == full).all()
    # The plunge itself is judged once it has past rates to
    # compare against.
    judged = anomaly.flag_knee(_soh_frame(plunging))
    assert judged["knee_anomaly"].iloc[-5:].any()


def _relative_frame():
    rows = 8
    frame = pd.DataFrame({
        "cell_id": ["BTEST"] * rows,
        "discharge_index": np.arange(rows),
        "vw_capacity_ah": [0.0] * 5 + [2.0] * 3,
        "temperature_mean": [25.0] * rows,
        "ambient_temperature": [24.0] * rows,
        "current_mean": [2.0] * rows,
        "nominal_current_a": [2.0] * rows,
    })
    for column in (
        "vw_time_s",
        "vw_dqdv_peak_height",
        "vw_voltage_mean",
        "voltage_mean",
        "voltage_drop",
        "resistance_proxy_ohm",
        "discharge_duration_s",
    ):
        frame[column] = 1.0
    return frame


def test_zero_opening_baseline_falls_back_without_future_data():
    """A zero opening baseline must fall back to 1.0 (the
    window_model_row rule), not to the cell's whole-lifetime
    median — the old fill read the cell's future and, here,
    divided by zero."""

    out = run_experiments.cell_relative_features(
        _relative_frame()
    )

    ratios = out["vw_capacity_ratio"].to_numpy(dtype=float)
    assert np.isfinite(ratios).all()
    assert ratios[-1] == pytest.approx(2.0)


def test_pack_evaluate_rejects_prediction_gaps(tmp_path):
    """A cycle with no SOH prediction is a pipeline error,
    not a row to score silently."""

    clean = pd.DataFrame({
        "cell_id": ["B0001"] * 3,
        "cycle": [1, 2, 3],
        "soh": [100.0, 99.0, 98.0],
    })
    clean.to_csv(
        tmp_path / "nasa_ml_dataset_clean.csv", index=False
    )
    predictions = pd.DataFrame({
        "cell_id": ["B0001"] * 2,
        "cycle": [1, 2],
        "predicted_soh": [99.5, 98.5],
    })
    predictions.to_csv(
        tmp_path / "soh_predictions_B0001.csv", index=False
    )

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pack_evaluate, "PROC", tmp_path)
        with pytest.raises(RuntimeError, match="no SOH prediction"):
            pack_evaluate.load_df()
