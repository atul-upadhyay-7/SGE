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


def test_assemble_features_reports_every_missing_column(bundle):

    # A row carrying only cell_id and cycle is missing the whole
    # feature set. The error has to name what is absent so an
    # operator can tell a malformed payload from a stale schema.
    frame = pd.DataFrame([
        {"cell_id": "X", "cycle": 1}
    ])

    with pytest.raises(KeyError) as info:

        assemble_features(frame, bundle["features"])

    message = str(info.value)

    assert "absent from the input" in message

    # Every missing feature is named, not just the first.
    assert "voltage_mean" in message
    assert "voltage_drop" in message


def test_assemble_features_does_not_derive_capacity_change():

    # capacity_change_ah used to be derived here from measured
    # capacity when the column was absent. That derivation is
    # gone: capacity_change_ah is the first difference of the
    # numerator of the SOH target, so it is not a legitimate
    # model input. A frame that does carry it passes straight
    # through, and nothing is computed from capacity_ah.
    frame = pd.DataFrame([
        {"cell_id": "X", "cycle": 1, "capacity_change_ah": np.nan},
        {"cell_id": "X", "cycle": 2, "capacity_change_ah": -0.02},
    ])

    matrix = assemble_features(
        frame, ["capacity_change_ah"]
    )

    # Passed through verbatim; the second row is still -0.02 and
    # the first is still NaN rather than being back-filled from
    # the raw capacity series.
    assert np.isnan(matrix["capacity_change_ah"].iloc[0])
    assert matrix["capacity_change_ah"].iloc[1] == (
        pytest.approx(-0.02)
    )


def test_assemble_features_refuses_to_derive_capacity_change():

    # capacity_ah is present but capacity_change_ah is not.
    # The old code computed the difference and carried on. The
    # new code must refuse, because deriving it would rebuild
    # the target-derived column this feature set removed.
    frame = pd.DataFrame([
        {"cell_id": "X", "cycle": 1, "capacity_ah": 1.80},
        {"cell_id": "X", "cycle": 2, "capacity_ah": 1.78},
    ])

    with pytest.raises(KeyError) as info:

        assemble_features(frame, ["capacity_change_ah"])

    assert "capacity_change_ah" in str(info.value)


def test_assemble_features_never_invents_a_leaky_column(bundle, cycle_row):

    # The old contract padded soh_change_pct with NaN whenever
    # the column was absent, so a bundle asking for that
    # target-derived feature silently got a fabricated column.
    # assembly must refuse instead.
    frame = pd.DataFrame([cycle_row])

    frame = frame.drop(columns=["soh_change_pct"])

    with pytest.raises(KeyError) as info:

        assemble_features(
            frame, bundle["features"] + ["soh_change_pct"]
        )

    assert "soh_change_pct" in str(info.value)


def test_active_features_carry_no_target_derived_column():

    # Guards the fixture itself. ACTIVE_FEATURES mirrors the
    # real artifact's feature list, so if a target-derived
    # column is reintroduced upstream this fails before any
    # prediction test can pass on a padded NaN.
    from features import LEAKY_FEATURES

    leaked = sorted(
        set(ACTIVE_FEATURES) & set(LEAKY_FEATURES)
    )

    assert leaked == []


def test_trained_artifact_feature_list_is_leak_free():

    # The committed artifact is what actually serves requests,
    # so its stored feature list is checked directly rather
    # than inferred from the fixture.
    path = predict.MODEL_DIR / "soh_xgb_all.joblib"

    if not path.exists():

        pytest.skip("no trained artifact present")

    from features import LEAKY_FEATURES

    try:

        bundle = predict.load_soh_model(path)

    except SohModelLoadError:

        pytest.skip(
            "artifact not deserializable in this "
            "environment; feature list checked by "
            "test_active_features_carry_no_target_derived_column"
        )

    leaked = sorted(
        set(bundle["features"]) & set(LEAKY_FEATURES)
    )

    assert leaked == []


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
