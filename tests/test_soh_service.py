"""
test_soh_service.py
-------------------
Latching, status reporting and the per-sample cost.

The point of the service is that the per-second path is cheap
and never blocks, and that it never invents a reading. Both are
asserted here rather than assumed.
"""

import time

import pytest

from conftest import discharge_samples, feed

import soh_service
from soh_service import (
    FAILED,
    OK,
    STALE,
    WARMING_UP,
    SohService,
    check_model_available
)


def close_with_charge(tracker, start=10_000.0, n=5):

    emitted = []

    for i in range(n):

        row = tracker.add_sample(
            start + i, 4.0, 1.5, 30.0
        )

        if row is not None:

            emitted.append(row)

    return emitted


def test_no_reading_before_the_first_cycle(bundle):

    service = SohService(bundle=bundle, cell_id="S1")

    state = service.read()

    assert state["soh"] is None
    assert state["status"] == WARMING_UP
    assert state["cycle"] is None
    assert state["cycles_seen"] == 0

    # A device acting on a fabricated number is worse off than
    # one that knows there is nothing yet, so nothing is
    # invented here.
    with pytest.raises(RuntimeError):

        service.warm_up_check()


def test_a_cycle_produces_a_reading(bundle):

    service = SohService(bundle=bundle, cell_id="S2")

    feed(service.tracker, discharge_samples(n=200))

    row = close_with_charge(service.tracker)[0]

    state = service.predict_cycle(row)

    assert state["soh"] is not None
    assert state["status"] == OK
    assert state["cycles_seen"] == 1
    assert state["cell_id"] == "S2"


def test_the_value_is_latched_between_cycles(bundle):

    service = SohService(bundle=bundle, cell_id="S3")

    feed(service.tracker, discharge_samples(n=200))

    service.predict_cycle(
        close_with_charge(service.tracker)[0]
    )

    first = service.read()["soh"]

    # Thousands of per-second reads return the same number
    # without re-predicting.
    for i in range(5000):

        assert service.add_sample(
            float(i), 4.0, 1.5, 30.0
        ) is None

    state = service.read()

    assert state["soh"] == first
    assert state["cycles_seen"] == 1
    assert state["phase"] == "charge"


def test_add_sample_returns_a_prediction_only_on_a_close(bundle):

    service = SohService(bundle=bundle, cell_id="S4")

    results = []

    for timestamp, voltage, current, temperature in (
        discharge_samples(n=200)
    ):

        results.append(service.add_sample(
            timestamp, voltage, current, temperature
        ))

    assert all(r is None for r in results)

    # Closing goes through the service, so the caller gets a
    # prediction rather than a bare feature row.
    emitted = [
        service.add_sample(
            10_000.0 + i, 4.0, 1.5, 30.0
        )
        for i in range(5)
    ]

    emitted = [r for r in emitted if r is not None]

    assert len(emitted) == 1
    assert emitted[0]["soh"] is not None
    assert emitted[0]["status"] == OK


def test_a_failure_keeps_the_previous_reading_and_says_why(bundle):

    service = SohService(bundle=bundle, cell_id="S5")

    feed(service.tracker, discharge_samples(n=200))

    service.predict_cycle(
        close_with_charge(service.tracker)[0]
    )

    good = service.read()["soh"]

    # A row missing every feature the model needs.
    with pytest.raises(KeyError):

        service.predict_cycle({
            "cell_id": "S5", "cycle": 2
        })

    state = service.read()

    # A stale reading beats none, but it must be visible.
    assert state["soh"] == good
    assert state["status"] == FAILED
    assert "KeyError" in state["error"]


def test_stale_after_the_configured_age(bundle, monkeypatch):

    service = SohService(
        bundle=bundle, cell_id="S6",
        stale_after_s=100.0
    )

    feed(service.tracker, discharge_samples(n=200))

    service.predict_cycle(
        close_with_charge(service.tracker)[0]
    )

    assert service.read()["status"] == OK

    # A latch with no expiry is a silent failure mode: if the
    # stream stops, the device keeps acting on a number that no
    # longer reflects the battery.
    real_time = time.time()
    monkeypatch.setattr(
        soh_service.time, "time",
        lambda: real_time + 500.0
    )

    state = service.read()

    assert state["status"] == STALE
    assert state["soh"] is not None
    assert state["age_s"] == pytest.approx(500.0, abs=1.0)


def test_staleness_can_be_disabled(bundle, monkeypatch):

    service = SohService(
        bundle=bundle, cell_id="S7",
        stale_after_s=None
    )

    feed(service.tracker, discharge_samples(n=200))

    service.predict_cycle(
        close_with_charge(service.tracker)[0]
    )

    real_time = time.time()
    monkeypatch.setattr(
        soh_service.time, "time",
        lambda: real_time + 10_000.0
    )

    assert service.read()["status"] == OK


def test_flush_emits_a_final_unclosable_cycle(bundle):

    service = SohService(bundle=bundle, cell_id="S8")

    feed(service.tracker, discharge_samples(n=200))

    assert service.read()["soh"] is None

    state = service.flush()

    assert state is not None
    assert state["soh"] is not None
    assert state["cycles_seen"] == 1

    assert service.flush() is None


def test_warm_up_check_passes_after_a_cycle(bundle):

    service = SohService(bundle=bundle, cell_id="S9")

    feed(service.tracker, discharge_samples(n=200))

    service.predict_cycle(
        close_with_charge(service.tracker)[0]
    )

    assert service.warm_up_check() is True


def test_warm_up_check_reports_a_failed_state(bundle):

    service = SohService(bundle=bundle, cell_id="S10")

    feed(service.tracker, discharge_samples(n=200))

    service.predict_cycle(
        close_with_charge(service.tracker)[0]
    )

    with pytest.raises(KeyError):

        service.predict_cycle({"cell_id": "S10"})

    with pytest.raises(RuntimeError) as info:

        service.warm_up_check()

    assert "failed state" in str(info.value)


def test_model_is_loaded_once_not_per_prediction(bundle, monkeypatch):

    service = SohService(bundle=bundle, cell_id="S11")

    calls = []
    real = soh_service.predict_soh

    def counting(*args, **kwargs):

        calls.append(1)

        return real(*args, **kwargs)

    monkeypatch.setattr(
        soh_service, "predict_soh", counting
    )

    for cycle in range(5):

        feed(
            service.tracker,
            discharge_samples(
                n=200, start_time=cycle * 5000.0
            )
        )

        service.predict_cycle(
            close_with_charge(
                service.tracker,
                start=cycle * 5000.0 + 3000.0
            )[0]
        )

    # A cold load is about 1.7 s, longer than the whole
    # one-second budget, so reloading per request cannot work.
    assert len(calls) == 5

    for _ in range(100):

        service.read()

    assert len(calls) == 5


def test_per_sample_cost_is_far_below_the_one_second_budget(bundle):

    service = SohService(bundle=bundle, cell_id="S12")

    samples = discharge_samples(n=200)

    # Warm up so the measurement is not dominated by first-call
    # import and allocation costs.
    feed(service.tracker, samples[:50])

    # Start above the warm-up so every measured sample is
    # accepted. The tracker drops any sample whose timestamp does
    # not advance, and a rejected sample is cheaper than an
    # accepted one, so measuring across rejections would flatter
    # the number rather than measure it.
    base = samples[49][0] + 1.0

    iterations = 20_000
    started = time.perf_counter()

    for i in range(iterations):

        service.tracker.add_sample(
            base + i, 3.6, 1.5, 30.0
        )

    per_sample_ms = (
        (time.perf_counter() - started)
        / iterations * 1000.0
    )

    assert service.tracker.out_of_order_samples == 0

    # The device publishes once a second, so the per-sample path
    # has a 1000 ms budget. It uses a small fraction of it, which
    # is the whole point of predicting per cycle instead.
    assert per_sample_ms < 1.0, (
        f"per-sample cost {per_sample_ms:.4f} ms"
    )


def test_predict_cycle_is_cheap(bundle):

    service = SohService(bundle=bundle, cell_id="S13")

    durations = []

    # One clock for the whole test, only ever moving forward.
    # The tracker rejects any sample whose timestamp does not
    # advance, so restarting the clock per cycle would silently
    # feed it nothing and measure an empty loop.
    clock = 0.0

    for cycle in range(51):

        start = clock

        feed(
            service.tracker,
            discharge_samples(n=200, start_time=start)
        )

        close = close_with_charge(
            service.tracker, start=start + 1000.0
        )

        assert close, (
            f"cycle {cycle} closed nothing at "
            f"t={start}"
        )

        # Advance past everything this cycle consumed, plus a
        # gap, so the next cycle cannot overlap it.
        clock = start + 5000.0

        state = service.predict_cycle(close[0])

        if cycle == 0:

            # First prediction pays any lazy initialisation.
            continue

        durations.append(state["predict_duration_ms"])

    mean_ms = sum(durations) / len(durations)

    assert service.tracker.out_of_order_samples == 0
    assert mean_ms < 50.0, f"mean {mean_ms:.3f} ms"


def test_check_model_available_reports_the_real_artifact():

    ok, message = check_model_available()

    # Whether the committed artifact loads depends on the
    # XGBoost build it was serialized with, which is not
    # something this suite controls. Both outcomes are
    # acceptable, so the assertion is on the contract rather
    # than on which one occurs: the call must answer, and it
    # must answer in words rather than raising out of a
    # sample loop.
    assert isinstance(ok, bool)
    assert isinstance(message, str)
    assert message

    if ok:
        assert "features" in message
    else:
        assert "not found" in message.lower() or "xgboost" in message.lower()


def test_check_model_available_fails_cleanly_on_a_missing_model():

    ok, message = check_model_available(
        "models/does_not_exist.joblib"
    )

    # A missing file is the common startup failure and must
    # name the remedy rather than raising.
    assert ok is False
    assert "does_not_exist.joblib" in message
    assert "train_soh.py" in message
