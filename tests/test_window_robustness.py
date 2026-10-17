"""
test_window_robustness.py
-------------------------
The report claims the partial-window SOH model tolerates sensor noise
but not a shorter window, scored on held-out cells only.

``data/processed/soh_window_robustness*.csv`` are the artifacts. These
tests pin the claim five ways:

  * the clean level reproduces the committed partial-transfer MAEs
    exactly, so the robustness protocol is the same held-out task and
    not a new, easier one;
  * every reported level reproduces from a fresh run;
  * voltage noise moves the MAE in the expected direction and the
    largest level stays under the budget, while current noise barely
    moves it;
  * both shorter windows exceed the budget;
  * the two derived answers (smallest window, largest noise) follow
    from the summary, and docs/RESULTS.md carries the section.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

PROC = REPO / "data" / "processed"
DETAIL = PROC / "soh_window_robustness.csv"
SUMMARY = PROC / "soh_window_robustness_summary.csv"
LIMITS = PROC / "soh_window_robustness_limits.csv"
PARTIAL = PROC / "experiments" / "soh_partial_transfer.csv"
RESULTS = REPO / "docs" / "RESULTS.md"

TOL = 0.01


@pytest.fixture(scope="module")
def summary():
    if not SUMMARY.exists():
        pytest.skip("window_robustness.py has not produced its CSVs")
    return pd.read_csv(SUMMARY)


@pytest.fixture(scope="module")
def limits():
    if not LIMITS.exists():
        pytest.skip("window_robustness.py has not produced its CSVs")
    return pd.read_csv(LIMITS)


@pytest.fixture(scope="module")
def fresh():
    """One full recomputation, shared by the tests that need it."""

    import window_robustness as WR

    try:
        return WR.robustness_table()
    except FileNotFoundError:
        pytest.skip("raw .mat dataset not present")


def _pooled(frame, model="XGBoost"):
    block = frame[frame["model"] == model]
    weights = block["n_cycles"].to_numpy(dtype=float)
    values = block["mae_pct"].to_numpy(dtype=float)
    return float((values * weights).sum() / weights.sum())


def test_detail_covers_every_cell_condition_and_model():
    """Nine held-out cells, nine conditions, three methods, all present."""

    detail = pd.read_csv(DETAIL)

    assert set(detail["test_cell"]) == {
        "B0005", "B0006", "B0007", "B0018",
        "B0029", "B0030", "B0031", "B0032", "B0053",
    }
    assert set(detail["model"]) == {"ElasticNet", "XGBoost", "window_formula"}
    assert detail["condition"].nunique() == 9
    assert set(detail["kind"]) == {"clean", "voltage", "current", "window"}

    # Every held-out cell is scored under every condition by every model.
    assert len(detail) == 9 * 9 * 3


def test_clean_level_reproduces_the_committed_partial_transfer(summary):
    """The clean condition is the same held-out task already in the
    report, so its pooled MAE must equal the transfer table's."""

    partial = pd.read_csv(PARTIAL)
    clean = summary[summary["condition"] == "clean"].iloc[0]

    for model, column in (
        ("XGBoost", "xgb_mae_pct"),
        ("ElasticNet", "elasticnet_mae_pct"),
        ("window_formula", "window_formula_mae_pct"),
    ):
        assert abs(_pooled(partial, model) - float(clean[column])) <= TOL


def test_reported_levels_reproduce_from_a_fresh_run(summary, fresh):
    """A fresh run of the whole stage must match the committed summary."""

    _, fresh_summary, _ = fresh

    left = summary.set_index("condition").sort_index()
    right = fresh_summary.set_index("condition").sort_index()

    assert list(left.index) == list(right.index)

    for column in (
        "xgb_mae_pct", "elasticnet_mae_pct", "window_formula_mae_pct",
    ):
        assert (left[column] - right[column]).abs().max() <= TOL, column


def test_voltage_noise_is_monotone_and_stays_under_the_budget(limits):
    """More voltage noise is worse, and even the largest level holds."""

    summary = pd.read_csv(SUMMARY)
    limit = float(limits["mae_limit_pct"].iloc[0])

    voltages = summary[summary["kind"] == "voltage"].sort_values(
        "voltage_noise_mv"
    )

    assert list(voltages["voltage_noise_mv"]) == [5.0, 10.0, 20.0]
    assert list(voltages["xgb_mae_pct"]) == sorted(voltages["xgb_mae_pct"])
    assert voltages["xgb_mae_pct"].max() < limit

    # Current noise is negligible: the window capacity is an integral, so
    # zero-mean multiplicative noise averages out.
    currents = summary[summary["kind"] == "current"]
    clean = float(summary.loc[summary["condition"] == "clean", "xgb_mae_pct"].iloc[0])
    assert (currents["xgb_mae_pct"] - clean).abs().max() < 0.1


def test_shorter_windows_exceed_the_budget(limits):
    """The finding: narrowing the window breaks the held-out MAE."""

    summary = pd.read_csv(SUMMARY)
    limit = float(limits["mae_limit_pct"].iloc[0])

    windows = summary[summary["kind"] == "window"]

    assert list(windows["window_span_v"]) == [0.4, 0.2]
    assert (windows["xgb_mae_pct"] > limit).all()


def test_limits_follow_from_the_summary(summary, limits):
    """The smallest working window is the trained band, and the largest
    noise that holds is the largest level tried."""

    limit = float(limits["mae_limit_pct"].iloc[0])
    row = limits.iloc[0]

    # Nothing narrower than the trained band is under the limit, so the
    # smallest surviving window is the trained 4.0-3.4 V band.
    under = summary[summary["xgb_mae_pct"] < limit]

    assert float(row["smallest_window_span_v"]) == under[
        under["kind"].isin(["clean", "window"])
    ]["window_span_v"].min()
    assert float(row["smallest_window_high_v"]) == 4.0
    assert float(row["smallest_window_low_v"]) == 3.4

    voltage_under = under[under["kind"] == "voltage"]
    current_under = under[under["kind"] == "current"]
    assert float(row["largest_voltage_noise_mv"]) == voltage_under[
        "voltage_noise_mv"
    ].max()
    assert float(row["largest_current_noise_pct"]) == current_under[
        "current_noise_pct"
    ].max()


def test_perturbation_is_seeded_and_deterministic():
    """Same seed, same noise; a different seed is a different draw."""

    import window_robustness as WR

    condition = next(
        c for c in WR.conditions() if c["condition"] == "voltage_noise_20mv"
    )

    trace = {
        "cell_id": "X",
        "cycle_number": 1,
        "voltage": np.linspace(4.2, 2.5, 200),
        "current": np.full(200, -2.0),
        "temperature": np.full(200, 24.0),
        "time": np.arange(200, dtype=float),
        "capacity": 2.0,
        "ambient_temperature": 24.0,
    }

    first = WR.perturbed_cell_frame([trace], condition, (1, 2, 3))
    second = WR.perturbed_cell_frame([trace], condition, (1, 2, 3))
    other = WR.perturbed_cell_frame([trace], condition, (1, 2, 4))

    assert first.equals(second)
    assert not first.equals(other)


def test_readme_reports_the_section(summary):
    """The generated region must carry the section and the limit level."""

    text = RESULTS.read_text()
    band = summary[summary["condition"] == "window_3.8_3.6"].iloc[0]

    assert "Noise and shorter-window robustness" in text
    assert f"{float(band['xgb_mae_pct']):.2f}" in text
