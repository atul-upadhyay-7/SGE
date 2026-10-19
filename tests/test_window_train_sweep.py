"""
test_window_train_sweep.py
--------------------------
The robustness test fed shorter bands to the *shipped* 4.0-3.4 V model
and watched the pooled MAE climb above budget; the README used to read
that as "a shorter window does not work". This test pins the fairer
experiment instead: a model trained on each candidate window, scored
leave-one-cell-out on G1+G3 and by training G1+G3 and testing B0053 and
the four random-load cells.

``data/processed/soh_window_train_sweep*.csv`` are the artifacts
(src/window_train_sweep.py). These tests check:

  * the trained 4.0-3.4 V band reproduces the offline cell-relative
    feature path exactly, so the sweep is scored on the shipped task;
  * every candidate window is scored for both models on all thirteen
    held-out cells;
  * with its own model every window, including the 0.20 V inset, keeps
    the pooled held-out MAE under the 3% budget;
  * the smallest surviving window is the 3.8-3.6 V inset, and the
    random-load cells are the stricter subset;
  * docs/RESULTS.md and the README carry the result.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

PROC = REPO / "data" / "processed"
CLEAN = PROC / "nasa_ml_dataset_clean.csv"
DETAIL = PROC / "soh_window_train_sweep.csv"
SUMMARY = PROC / "soh_window_train_sweep_summary.csv"
LIMITS = PROC / "soh_window_train_sweep_limits.csv"
RESULTS = REPO / "docs" / "RESULTS.md"
README = REPO / "README.md"

WINDOWS = (
    ("window_4.0_3.4", 4.0, 3.4),
    ("window_3.9_3.5", 3.9, 3.5),
    ("window_3.8_3.6", 3.8, 3.6),
    ("window_4.1_3.7", 4.1, 3.7),
    ("window_3.7_3.4", 3.7, 3.4),
)

TOL = 1e-6
LIMIT = 3.0

UNSTABLE = "unstable"
R2_FLOOR = -1000.0
MAE_CEILING = 100.0


@pytest.fixture(scope="module")
def detail():
    if not DETAIL.exists():
        pytest.skip("window_train_sweep.py has not produced its CSVs")
    return pd.read_csv(DETAIL)


@pytest.fixture(scope="module")
def summary():
    if not SUMMARY.exists():
        pytest.skip("window_train_sweep.py has not produced its CSVs")
    return pd.read_csv(SUMMARY)


@pytest.fixture(scope="module")
def limits():
    if not LIMITS.exists():
        pytest.skip("window_train_sweep.py has not produced its CSVs")
    return pd.read_csv(LIMITS)


@pytest.fixture(scope="module")
def offline():
    """
    The nine constant-current held-out cells scored through the existing
    offline feature path (run_experiments.cell_relative_features), which
    reads the 4.0-3.4 V window columns already stored in the clean
    dataset. Used to confirm the sweep's trained band is the same task.
    """

    import run_experiments as RE
    from evaluation import build_model
    from features import WINDOW_MODEL_FEATURES as FEATURES

    clean = pd.read_csv(CLEAN)
    data = RE.window_formula(RE.cell_relative_features(clean))

    rows = []

    def score(test, train):
        model = build_model("XGBoost")
        model.fit(train[list(FEATURES)], train["soh"])
        predicted = model.predict(test[list(FEATURES)])
        rows.append({
            "test_cell": test["cell_id"].iloc[0],
            "n_cycles": int(len(test)),
            "mae_pct": float(np.mean(np.abs(test["soh"] - predicted))),
        })

    for group in ("G1", "G3"):
        g = data[data["group"] == group]
        for cell in sorted(g["cell_id"].unique()):
            score(g[g["cell_id"] == cell], g[g["cell_id"] != cell])

    score(data[data["group"] == "G9"], data[data["group"].isin(["G1", "G3"])])

    return pd.DataFrame(rows)


def test_candidate_windows_are_the_requested_bands(summary):
    assert list(summary["window"]) == [name for name, _, _ in WINDOWS]

    for name, high, low in WINDOWS:
        row = summary[summary["window"] == name].iloc[0]
        assert row["window_high_v"] == high
        assert row["window_low_v"] == low
        assert np.isclose(row["window_span_v"], high - low, atol=TOL)


def test_trained_band_reproduces_the_offline_feature_path(detail, offline):
    """The 4.0-3.4 V rows must be the shipped partial-discharge task, not
    a new one: rebuild them through cell_relative_features and compare."""

    band = detail[
        (detail["window"] == "window_4.0_3.4")
        & (detail["model"] == "XGBoost")
        & (~detail["is_random_load"])
    ].set_index("test_cell")

    assert set(band.index) == set(offline["test_cell"])

    for _, row in offline.iterrows():
        assert np.isclose(
            float(band.loc[row["test_cell"], "mae_pct"]),
            row["mae_pct"],
            rtol=0.0,
            atol=1e-6,
        ), f"{row['test_cell']}: sweep MAE disagrees with the offline path"


def test_every_window_scores_both_models_on_all_cells(detail):
    for name, _high, _low in WINDOWS:

        block = detail[detail["window"] == name]

        assert set(block["model"]) == {
            "XGBoost", "ElasticNet", "window_formula"
        }

        per_model = block[block["model"] == "XGBoost"]

        assert per_model["test_cell"].nunique() == 13
        assert per_model["n_cycles"].min() > 0


def test_every_window_survives_with_its_own_model(summary):
    assert (summary["xgb_mae_pct"] < LIMIT).all(), (
        "with a model of its own every band should hold the budget on "
        "the constant-current held-out cells"
    )
    assert (summary["xgb_mae_all_pct"] < LIMIT).all()


def test_smallest_surviving_window_is_the_tightest_inset(limits, summary):
    row = limits.iloc[0]

    assert row["smallest_under_limit_window"] == "window_3.8_3.6"
    assert np.isclose(row["smallest_under_limit_span_v"], 0.2, atol=TOL)

    under = summary[summary["xgb_mae_all_pct"] < LIMIT]

    assert row["smallest_under_limit_span_v"] == under["window_span_v"].min()
    assert np.isclose(
        row["smallest_under_limit_mae_pct"],
        summary.loc[
            summary["window"] == "window_3.8_3.6", "xgb_mae_all_pct"
        ].iloc[0],
        atol=TOL,
    )


def test_random_load_cells_are_the_stricter_subset(summary):
    trained = summary[summary["window"] == "window_4.0_3.4"].iloc[0]
    raised = summary[summary["window"] == "window_4.1_3.7"].iloc[0]

    assert trained["xgb_mae_random_load_pct"] < LIMIT
    assert raised["xgb_mae_random_load_pct"] > LIMIT


def test_results_document_carries_the_fair_test(summary, limits):
    text = RESULTS.read_text()

    assert "A model trained on its own window" in text

    small = summary[
        summary["window"] == limits.iloc[0]["smallest_under_limit_window"]
    ].iloc[0]

    assert (
        f"| {small['window_high_v']:.1f}–{small['window_low_v']:.1f} | "
        f"{small['window_span_v']:.2f} |"
    ) in text


def test_readme_row_carries_the_smallest_window(summary, limits):
    text = README.read_text()

    assert "a shorter window works if trained on it" in text

    small = summary[
        summary["window"] == limits.iloc[0]["smallest_under_limit_window"]
    ].iloc[0]

    assert f"{small['window_span_v']:.2f} V window" in text
    assert (
        f"{limits.iloc[0]['smallest_under_limit_mae_pct']:.2f}% pooled"
        in text
    )


def test_no_unstable_metric_is_written_as_a_raw_number(detail, summary):
    """A score that leaves the sane range is written as the word
    `unstable`, not as the raw float, because at that magnitude the
    digits are floating-point noise that a rerun can change."""

    for column, floor, ceiling in (
        ("mae_pct", None, MAE_CEILING),
        ("r2", R2_FLOOR, None),
    ):

        for value in detail[column]:

            if value == UNSTABLE:
                continue

            number = float(value)

            if floor is not None:
                assert number >= floor, (column, value)

            if ceiling is not None:
                assert number <= ceiling, (column, value)

    # The flagged rows exist, so the guard is exercised and not vacuous.
    assert (detail["mae_pct"] == UNSTABLE).any()
    assert (detail["r2"] == UNSTABLE).any()

    summary_metrics = (
        "xgb_mae_pct",
        "elasticnet_mae_pct",
        "window_formula_mae_pct",
        "xgb_mae_random_load_pct",
        "elasticnet_mae_random_load_pct",
        "xgb_mae_all_pct",
        "elasticnet_mae_all_pct",
    )

    for column in summary_metrics:

        for value in summary[column]:

            if value == UNSTABLE:
                continue

            assert float(value) <= MAE_CEILING, (column, value)


def test_results_document_explains_the_unstable_flag():
    text = RESULTS.read_text()

    assert "unstable*" in text or "as *unstable*" in text
    assert "-1000" in text
    assert "100%" in text


def test_the_stage_writes_byte_identical_files_twice(tmp_path, monkeypatch):
    """Run the stage twice and compare bytes: the fix for the clean-tree
    failure is that every written float is a pure, rounded function of
    the inputs, so two runs cannot differ in the last digits.

    The workload is trimmed to one band and four constant-current cells
    so the test stays affordable; the code path (sweep_table + the
    rounded/stable write) is the stage's own.
    """

    import window_train_sweep as W

    clean = pd.read_csv(CLEAN)
    cells = ("B0005", "B0006", "B0029", "B0030")
    trimmed = tmp_path / "clean.csv"
    clean[clean["cell_id"].isin(cells)].to_csv(trimmed, index=False)

    monkeypatch.setattr(W, "CLEAN", trimmed)
    monkeypatch.setattr(W, "BANDS", (W.BANDS[0],))
    monkeypatch.setattr(W, "RANDOM_LOAD_CELLS", ())

    # Pass the raw directory by absolute path: resolve_raw_dir() uses
    # paths relative to the cwd, and an unrelated test (train.py's
    # driver) leaves the cwd elsewhere.
    raw = REPO / "data" / "raw"

    outputs = {
        name: tmp_path / f"{name}.csv"
        for name in ("detail", "summary", "limits")
    }

    monkeypatch.setattr(W, "DETAIL_OUT", outputs["detail"])
    monkeypatch.setattr(W, "SUMMARY_OUT", outputs["summary"])
    monkeypatch.setattr(W, "LIMITS_OUT", outputs["limits"])

    def run():
        detail, summary, limits = W.sweep_table(raw_dir=raw)
        W.write_outputs(detail, summary, limits)
        return {name: path.read_bytes() for name, path in outputs.items()}

    first = run()
    second = run()

    assert first == second, "a second run changed the written bytes"
    assert all(first.values()), "the stage wrote an empty file"
