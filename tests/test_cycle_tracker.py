"""
test_cycle_tracker.py
---------------------
The cycle boundary rules, tested against the failure modes
actually observed in this dataset rather than against tidy
synthetic cycles.
"""

import numpy as np
import pytest

from conftest import discharge_samples, feed

from cycle_tracker import (
    CHARGE_DEBOUNCE_SAMPLES,
    DEFAULT_MAX_SAMPLES,
    DISCHARGE_DEBOUNCE_SAMPLES,
    CycleTracker
)


def test_a_plain_discharge_emits_one_row_on_charge():

    tracker = CycleTracker(cell_id="T1")

    assert feed(
        tracker, discharge_samples(n=200)
    ) == []

    # Charge phase closes it, but only once the positive run
    # confirms, so nothing is emitted on the first samples.
    emitted = []

    for i in range(5):

        row = tracker.add_sample(
            1000.0 + i, 4.0, 1.5, 30.0
        )

        if row is not None:

            emitted.append(row)

        if i < CHARGE_DEBOUNCE_SAMPLES - 1:

            assert row is None

    assert len(emitted) == 1
    assert tracker.cycle_index == 1


def test_single_negative_glitch_in_charge_emits_nothing():

    tracker = CycleTracker(cell_id="T2")

    # Charge, with the one-sample negative glitch that opens
    # every charge phase in this dataset.
    for i in range(200):

        current = -3.4 if i == 0 else 1.5

        assert tracker.add_sample(
            float(i), 3.6, current, 30.0
        ) is None

    assert tracker.cycle_index == 0


def test_glitch_after_a_discharge_does_not_leak_into_the_cycle():

    tracker = CycleTracker(cell_id="T3")

    # A discharge whose final negative run is one sample short
    # of the debounce, then the charge phase's leading rest and
    # its single negative glitch. Before the run was cleared on
    # a rest, the glitch completed a run of five and was pulled
    # into the cycle: it hit 23 of B0018's 132 cycles.
    samples = discharge_samples(n=199)
    samples += discharge_samples(
        n=1, current=0.0, start_time=600.0, voltage=3.4
    )
    samples += [
        (610.0, 3.45, 0.004, 30.0),     # charge's leading rest
        (613.0, 3.08, -3.49, 30.0),    # charge's negative glitch
    ]

    rows = []
    for timestamp, voltage, current, temperature in samples:

        row = tracker.add_sample(
            timestamp, voltage, current, temperature
        )

        if row is not None:

            rows.append(row)

    for i in range(5):

        rows.append(tracker.add_sample(
            700.0 + i, 3.6, 1.5, 30.0
        ))

    emitted = [r for r in rows if r is not None]

    assert len(emitted) == 1

    # The glitch is -3.49 A; a real discharge bottoms out near
    # -2.0 A, so this is the check that it stayed out.
    assert emitted[0]["current_min"] == pytest.approx(
        -2.0, abs=0.01
    )


def test_lead_in_rest_samples_are_claimed():

    tracker = CycleTracker(cell_id="T4")

    # Two resting samples, then the load, then charge.
    feed(tracker, [
        (0.0, 4.1881, 0.0001, 30.0),
        (3.0, 4.1882, 0.0015, 30.0),
    ])

    assert feed(
        tracker, discharge_samples(
            n=200, start_time=6.0, voltage=3.98
        )
    ) == []

    emitted = []

    for i in range(5):

        row = tracker.add_sample(
            900.0 + i, 4.0, 1.5, 30.0
        )

        if row is not None:

            emitted.append(row)

    assert len(emitted) == 1

    row = emitted[0]

    # The row must start at the resting voltage, not at the
    # first loaded sample. Omitting the lead-in measured a 44%
    # error in voltage_drop.
    assert row["voltage_start"] == pytest.approx(
        4.1881, abs=0.001
    )
    assert row["voltage_max"] == pytest.approx(
        4.1882, abs=0.001
    )


def test_sustained_rest_closes_and_is_excluded():

    tracker = CycleTracker(
        cell_id="T5", idle_close=20
    )

    feed(tracker, discharge_samples(n=100))

    # A long rest, as a device streaming through an impedance
    # phase would produce. This is the mechanism that separates
    # two discharges the dataset merges into one.
    for i in range(20):

        row = tracker.add_sample(
            500.0 + i, 3.9, 0.0, 30.0
        )

        if i < 19:

            assert row is None

    assert row is not None

    # The rest belongs to neither cycle.
    assert row["discharge_duration_s"] == pytest.approx(
        99 * 3.0, rel=0.01
    )

    # And a second discharge is detected normally afterwards.
    more = feed(
        tracker, discharge_samples(
            n=100, start_time=1000.0
        )
    )

    assert more == []

    row = tracker.add_sample(2000.0, 4.0, 1.5, 30.0)
    row = tracker.add_sample(2003.0, 4.0, 1.5, 30.0)
    row = tracker.add_sample(2006.0, 4.0, 1.5, 30.0)

    assert row is not None
    assert tracker.cycle_index == 2


def test_flush_emits_an_unclosable_final_discharge():

    tracker = CycleTracker(cell_id="T6")

    feed(tracker, discharge_samples(n=200))

    assert tracker.cycle_index == 0

    row = tracker.flush()

    assert row is not None
    assert tracker.cycle_index == 1

    # Nothing armed, so a second flush is a no-op.
    assert tracker.flush() is None


def test_capacity_is_integrated_in_amp_hours():

    tracker = CycleTracker(cell_id="T7")

    # 2 A for 600 s is 1/3 Ah. Integrating without the 3600
    # divisor gives 1200, which is amp-seconds.
    samples = [
        (float(i), 3.5, -2.0, 30.0)
        for i in range(601)
    ]

    feed(tracker, samples)

    row = tracker.flush()

    assert row["capacity_ah"] == pytest.approx(
        2.0 * 600.0 / 3600.0, rel=0.01
    )


def test_capacity_change_is_nan_on_the_first_cycle():

    tracker = CycleTracker(cell_id="T8")

    feed(tracker, discharge_samples(n=100))
    first = tracker.flush()

    assert np.isnan(first["capacity_change_ah"])

    feed(tracker, discharge_samples(
        n=100, start_time=1000.0, current=-1.9
    ))
    second = tracker.flush()

    assert second["capacity_change_ah"] == pytest.approx(
        second["capacity_ah"] - first["capacity_ah"],
        rel=1e-6
    )


def test_soh_change_is_never_inferred():

    tracker = CycleTracker(cell_id="T9")

    feed(tracker, discharge_samples(n=100))
    row = tracker.flush()

    # It is the first difference of the target, so it cannot
    # exist for an unseen cell. The fitted imputer fills it.
    assert np.isnan(row["soh_change_pct"])


def test_too_few_samples_produces_no_row():

    tracker = CycleTracker(cell_id="T10")

    feed(tracker, discharge_samples(n=3))
    row = tracker.flush()

    assert row is None


def test_buffer_is_bounded_by_max_samples():

    tracker = CycleTracker(
        cell_id="T11", max_samples=50
    )

    # A stuck negative sensor never closes. The partial cycle
    # is emitted rather than the buffer growing without limit.
    rows = feed(
        tracker, discharge_samples(n=500)
    )

    assert len(rows) >= 1
    assert tracker.samples_in_discharge < 500


def test_default_bound_covers_a_real_long_discharge():

    # At 1 Hz this is six hours, against observed discharges of
    # 2792 to 3690 s. A bound under that would truncate healthy
    # cycles; the original 512 would have truncated all of them.
    assert DEFAULT_MAX_SAMPLES >= 6 * 3600
    assert DEFAULT_MAX_SAMPLES > 3690


def test_reset_clears_state():

    tracker = CycleTracker(cell_id="T12")

    feed(tracker, discharge_samples(n=100))
    tracker.flush()

    assert tracker.cycle_index == 1

    tracker.reset()

    assert tracker.cycle_index == 0
    assert tracker.samples_in_discharge == 0
    assert tracker.phase is None
    assert tracker.flush() is None


def test_debounce_rejects_a_short_negative_burst():

    tracker = CycleTracker(
        cell_id="T13",
        discharge_debounce=DISCHARGE_DEBOUNCE_SAMPLES
    )

    # Four samples is one short of arming, so they are held in
    # the debounce window rather than discarded.
    feed(tracker, discharge_samples(n=4))

    assert tracker.samples_in_discharge == 4

    # A resting sample proves nothing, so the window is dropped.
    tracker.add_sample(12.0, 3.9, 0.0, 30.0)

    assert tracker.samples_in_discharge == 0

    # The fifth consecutive sample arms it. The count is six
    # rather than five because arming also claims the resting
    # sample that preceded it as the cycle's lead-in.
    feed(tracker, discharge_samples(
        n=5, start_time=15.0
    ))

    assert tracker.samples_in_discharge == 6

    # Being armed is what matters, so confirm by closing it.
    emitted = feed(tracker, [
        (100.0 + i, 4.0, 1.5, 30.0)
        for i in range(5)
    ])

    assert len(emitted) == 1
