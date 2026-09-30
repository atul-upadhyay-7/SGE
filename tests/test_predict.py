"""
test_predict.py
---------------
Model loading and feature assembly.

The committed artifact cannot be loaded in this environment, so
the load-failure path is tested directly rather than being
skipped: it is the failure this deployment will actually hit,
and its error message is the only thing standing between an
operator and a silent stale reading.
"""

import numpy as np
import pandas as pd
import pytest

from conftest import ACTIVE_FEATURES, make_bundle

import predict
from predict import (
    SohModelLoadError,
    assemble_features,
    clear_model_cache,
    load_soh_model,
    predict_soh
)


@pytest.fixture(autouse=True)
def _clear_cache():

    clear_model_cache()

    yield

    clear_model_cache()


def test_missing_file_names_the_remedy(tmp_path):

    with pytest.raises(SohModelLoadError) as info:

        load_soh_model(tmp_path / "absent.joblib")

    message = str(info.value)

    assert "not found" in message
    assert "train_soh.py" in message


def test_corrupt_file_explains_the_xgboost_mismatch(tmp_path):

    # The real artifact fails with "input stream corrupted" from
    # XGBoost, so that branch of the message is what an operator
    # will see. Simulated here because reproducing a genuine
    # cross-version booster is not possible in a test.
    message = predict._explain_load_failure(
        "models/soh_xgb_all.joblib",
        Exception("input stream corrupted")
    )

    assert "Could not load" in message
    assert "serialized by a different" in message
    assert "xgboost" in message.lower()

    # The remedy has to be actionable, and the UBJ hint must
    # name the real API, get_booster(), not the C type.
    assert "get_booster()" in message
    assert "_Booster" not in message

    # A booster alone is not enough to predict, so the message
    # must say what else travels with it.
    assert "imputer" in message
    assert "feature order" in message


def test_unrecognised_load_failure_still_suggests_the_mismatch():

    message = predict._explain_load_failure(
        "models/soh_xgb_all.joblib",
        Exception("something else went wrong")
    )

    assert "Could not load" in message
    assert "xgboost" in message.lower()


def test_load_failure_reports_the_installed_version(tmp_path):

    bad = tmp_path / "soh_xgb_all.joblib"

    with open(bad, "wb") as handle:

        handle.write(b"not a joblib at all")

    with pytest.raises(SohModelLoadError) as info:

        load_soh_model(bad)

    import xgboost

    assert xgboost.__version__ in str(info.value)


def test_incomplete_bundle_is_rejected(tmp_path):

    path = tmp_path / "partial.joblib"

    import joblib

    joblib.dump({"model": object()}, path)

    with pytest.raises(SohModelLoadError) as info:

        load_soh_model(path)

    assert "missing required bundle keys" in str(info.value)
    assert "features" in str(info.value)
    assert "target" in str(info.value)


def test_non_dict_bundle_is_rejected(tmp_path):

    path = tmp_path / "list.joblib"

    import joblib

    joblib.dump([1, 2, 3], path)

    with pytest.raises(SohModelLoadError) as info:

        load_soh_model(path)

    assert "did not contain a bundle" in str(info.value)


def test_bundle_is_cached_across_calls(tmp_path, monkeypatch):

    path = tmp_path / "good.joblib"

    import joblib

    joblib.dump(make_bundle(), path)

    calls = []
    real_load = predict.joblib.load

    def counting_load(target):

        calls.append(str(target))

        return real_load(target)

    monkeypatch.setattr(
        predict.joblib, "load", counting_load
    )

    first = load_soh_model(path)
    second = load_soh_model(path)

    assert first is second
    assert len(calls) == 1


def test_cache_is_keyed_on_path(tmp_path):

    import joblib

    a = tmp_path / "a.joblib"
    b = tmp_path / "b.joblib"

    joblib.dump(make_bundle(value=10.0), a)
    joblib.dump(make_bundle(value=20.0), b)

    # Asking for a different model must not silently return the
    # cached one.
    assert load_soh_model(a)["model"] is not None

    value_a = predict_soh(load_soh_model(a), {
        f: 1.0 for f in ACTIVE_FEATURES
    })

    value_b = predict_soh(load_soh_model(b), {
        f: 1.0 for f in ACTIVE_FEATURES
    })

    assert value_a == pytest.approx(10.0)
    assert value_b == pytest.approx(20.0)


def test_assemble_features_orders_columns_exactly(bundle, cycle_row):

    frame = pd.DataFrame([cycle_row])

    # Deliberately reversed input order.
    frame = frame[list(reversed(frame.columns))]

    matrix = assemble_features(
        frame, bundle["features"]
    )

    assert list(matrix.columns) == list(
        bundle["features"]
    )


def test_assemble_features_reports_missing_columns(bundle, cycle_row):

    frame = pd.DataFrame([cycle_row])

    frame = frame.drop(columns=["voltage_mean"])

    with pytest.raises(KeyError) as info:

        assemble_features(frame, bundle["features"])

    assert "absent from the input" in str(info.value)
    assert "voltage_mean" in str(info.value)


def test_assemble_features_explains_an_underivable_capacity(bundle):

    frame = pd.DataFrame([
        {"cell_id": "X", "cycle": 1, "voltage_mean": 3.5}
    ])

    with pytest.raises(KeyError) as info:

        assemble_features(frame, bundle["features"])

    assert "capacity_ah" in str(info.value)


def test_assemble_features_derives_capacity_change(bundle):

    frame = pd.DataFrame([
        {"cell_id": "X", "cycle": 1, "capacity_ah": 1.80},
        {"cell_id": "X", "cycle": 2, "capacity_ah": 1.78},
    ])

    matrix = assemble_features(
        frame, ["capacity_change_ah"]
    )

    # First difference within the cell is undefined.
    assert np.isnan(matrix["capacity_change_ah"].iloc[0])
    assert matrix["capacity_change_ah"].iloc[1] == (
        pytest.approx(-0.02)
    )


def test_assemble_features_leaves_soh_change_nan(bundle, cycle_row):

    frame = pd.DataFrame([cycle_row])

    frame = frame.drop(columns=["soh_change_pct"])

    matrix = assemble_features(
        frame, bundle["features"]
    )

    # It is the first difference of the target, so it cannot be
    # derived for an unseen cell. The fitted imputer fills it.
    assert np.isnan(matrix["soh_change_pct"].iloc[0])


def test_predict_soh_accepts_a_dict(bundle, cycle_row):

    value = predict_soh(bundle, cycle_row)

    assert isinstance(value, float)
    assert np.isfinite(value)


def test_predict_soh_accepts_a_dataframe(bundle, cycle_row):

    value = predict_soh(
        bundle, pd.DataFrame([cycle_row])
    )

    assert np.isfinite(value)


def test_predict_soh_requires_features_for_a_bare_pipeline(bundle, cycle_row):

    with pytest.raises(ValueError) as info:

        predict_soh(bundle["model"], cycle_row)

    assert "features is required" in str(info.value)


def test_predict_soh_rejects_a_non_finite_result(cycle_row):

    class Broken:

        def predict(self, matrix):

            return np.array([np.nan])

    bundle = {
        "model": Broken(),
        "features": list(ACTIVE_FEATURES),
    }

    with pytest.raises(ValueError) as info:

        predict_soh(bundle, cycle_row)

    assert "non-finite" in str(info.value)
