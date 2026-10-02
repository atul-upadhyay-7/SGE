"""
test_leakage.py
---------------
Target and temporal leakage guards.

The point of these tests is not that the current feature list
happens to be clean. It is that reintroducing a leaky column
fails loudly, so a future edit cannot quietly restore the
optimistic score the leak produced. Each test injects a
target-derived column and asserts the pipeline refuses it.
"""

import numpy as np
import pandas as pd
import pytest

from conftest import ACTIVE_FEATURES

import evaluation
import features


# ----------------------------------------------------------------------
# The canonical lists themselves
# ----------------------------------------------------------------------

def test_evaluation_and_features_agree_on_what_is_leaky():

    # Two copies exist because evaluation.py imports nothing
    # from features.py (it is the shared scoring layer). They
    # must not drift, so this pins them equal.
    assert set(evaluation.LEAKY_FEATURES) == set(
        features.LEAKY_FEATURES
    )


def test_no_leaky_column_survives_in_the_real_feature_list():

    leaked = sorted(
        set(features.FEATURES) & set(features.LEAKY_FEATURES)
    )

    assert leaked == [], (
        "features.FEATURES contains target-derived columns: "
        f"{leaked}"
    )


def test_no_leaky_column_survives_in_the_model_input_fixture():

    leaked = sorted(
        set(ACTIVE_FEATURES) & set(features.LEAKY_FEATURES)
    )

    assert leaked == []


# ----------------------------------------------------------------------
# make_xy is the choke point every training script passes through
# ----------------------------------------------------------------------

@pytest.fixture
def tiny_frame():

    # Enough rows and variation that make_xy can build a matrix.
    return pd.DataFrame({
        "cell_id": ["A", "A", "B", "B"],
        "cycle": [1, 2, 1, 2],
        "soh": [100.0, 99.0, 100.0, 98.0],
        "capacity_ah": [2.0, 1.98, 2.0, 1.96],
        "soh_change_pct": [np.nan, -1.0, np.nan, -2.0],
        "capacity_change_ah": [np.nan, -0.02, np.nan, -0.04],
        "voltage_mean": [3.5, 3.5, 3.6, 3.5],
    })


@pytest.mark.parametrize("leaky", [
    "soh",
    "soh_change_pct",
    "capacity_change_ah",
    "capacity_ah",
    "capacity_fade_pct",
    "rul_cycles",
    "rul_cycles_80",
    "eol_cycle_threshold",
    "eol_cycle_observed",
    "is_pre_eol",
    "soh_slope_pct_per_cycle",
    "soh_slope_early_pct_per_cycle",
])
def test_make_xy_rejects_an_injected_leaky_feature(
    tiny_frame, monkeypatch, leaky
):

    # Inject exactly one leaky column into FEATURES, as a
    # careless future edit would, and require make_xy to raise
    # rather than return a matrix trained on the answer.
    monkeypatch.setattr(
        features,
        "FEATURES",
        features.FEATURES + [leaky]
    )

    # Ensure the injected column actually exists on the frame
    # so the failure is the leakage guard, not a missing column.
    frame = tiny_frame.copy()

    if leaky not in frame.columns:

        frame[leaky] = 1.0

    with pytest.raises(ValueError) as info:

        features.make_xy(frame, "soh")

    message = str(info.value)

    assert leaky in message
    assert "Target-derived" in message


def test_assert_no_leakage_names_the_offending_columns():

    with pytest.raises(ValueError) as info:

        evaluation.assert_no_leakage(
            ["voltage_mean", "soh_change_pct", "rul_cycles"],
            leaky_columns=evaluation.LEAKY_FEATURES
        )

    message = str(info.value)

    assert "soh_change_pct" in message
    assert "rul_cycles" in message


def test_assert_no_leakage_allows_a_narrower_list():

    # RUL predicts a function of the EOL cycle, so it narrows
    # the forbidden set. capacity_ah is allowed there.
    evaluation.assert_no_leakage(
        ["voltage_mean", "capacity_ah"],
        leaky_columns=[
            "eol_cycle_threshold",
            "rul_cycles_80",
        ]
    )


# ----------------------------------------------------------------------
# Temporal leakage
# ----------------------------------------------------------------------

def test_soh_and_capacity_are_flagged_as_leaky():
    """
    The two clearest temporal leaks: the target itself and its
    numerator. Both encode the answer at the row they describe.
    """

    assert "soh" in evaluation.LEAKY_FEATURES
    assert "capacity_ah" in evaluation.LEAKY_FEATURES


def test_full_lifetime_slopes_are_flagged_as_leaky():
    """
    soh_slope_pct_per_cycle is fitted over the whole lifetime,
    including cycles after t, so at cycle t it already knows
    the future. It must never be a predictive input.
    """

    assert "soh_slope_pct_per_cycle" in evaluation.LEAKY_FEATURES
    assert (
        "soh_slope_early_pct_per_cycle"
        in evaluation.LEAKY_FEATURES
    )


def test_evaluation_reuses_the_canonical_leaky_list():
    """
    evaluation.LEAKY_FEATURES and features.LEAKY_FEATURES must
    be one list, not two copies that can drift.

    They were duplicated once, and make_xy (features.py) and
    assert_no_leakage (evaluation.py) therefore guarded
    against different sets. Adding a leaky column to one list
    and not the other would weaken the guard with no test
    failing, which is precisely the failure this whole
    module exists to prevent.
    """

    assert evaluation.LEAKY_FEATURES is features.LEAKY_FEATURES


def test_the_leak_lists_agree_on_every_guard_under_test():
    """
    Two separate properties, which are easy to confuse:

    1. assert_no_leakage rejects every canonical entry.
    2. FEATURES, the list make_xy actually draws from,
       intersects the canonical list in nothing. This is the
       real guarantee: make_xy's guard only fires for columns
       someone has put into FEATURES, so the load-bearing
       invariant is that none of them are leaky to begin with.

    Property 2 is the one that matters, and it is what keeps a
    future edit from reintroducing a leak quietly.
    """

    import pandas as pd

    # (1) the reporting-side guard sees every entry.
    for column in features.LEAKY_FEATURES:

        with pytest.raises(ValueError):

            evaluation.assert_no_leakage([column])

    # (2) the training-side feature list is clean.
    assert not (
        set(features.FEATURES) & set(features.LEAKY_FEATURES)
    )

    # And make_xy therefore succeeds on a leaky-free frame.
    frame = pd.DataFrame(
        [
            {"soh": 90.0, "cycle": 1, "voltage_mean": 3.9},
            {"soh": 89.0, "cycle": 2, "voltage_mean": 3.8},
        ]
    )

    _, _, used = features.make_xy(frame, "soh")

    assert not (set(used) & set(features.LEAKY_FEATURES))
