"""
cycle_tracker.py
----------------
Turn a stream of per-second battery samples into per-cycle
feature rows.

Why cycles and not seconds
--------------------------
The SOH model consumes one row per cycle. Six of its
features are only defined once a cycle has finished
(voltage_end, voltage_drop, resistance_proxy_ohm,
discharge_duration_s, capacity_change_ah, soh_change_pct),
so no valid prediction exists until the cycle boundary.
The remaining fifteen are causal, but predicting from them
mid-cycle would mean scoring a partially-filled row, which
the model was never fit on.

SOH also moves slowly relative to the model's own error.
In this dataset a discharge runs roughly 2792 to 3690
seconds and SOH changes by -0.194 points per cycle on
average, about 6e-5 points per second, some 50000x below
the model's 3.23 point cross-cell error. A per-second
prediction would be reporting noise. The tracker therefore
emits one feature row per completed cycle and leaves the
service to hold the value in between.

Cycle boundary detection
------------------------
A discharge ends when the current crosses back through zero
into the charge phase. Discharge samples carry negative
current and charge samples positive. Boundary detection is
on the current sign rather than on elapsed time because
cycle length varies by more than 30% within a single cell,
so any fixed timer either truncates long cycles or merges
short ones.

A single negative sample does not start a discharge. Every
charge cycle in this dataset contains a negative glitch
(100% of them exactly one sample long, peaking near
-3.4 A), which would otherwise arm a phantom discharge and
later emit a feature row built from charge data. Real
discharges run 170 to 354 discharging samples, so requiring
DISCHARGE_DEBOUNCE_SAMPLES consecutive discharging samples
before arming separates the two by nearly two orders of
magnitude.

Sample ownership
----------------
Before a discharge is armed, only discharging samples are
collected, and a non-discharging sample throws the window
away. That is what rejects the charge glitches.

Once armed, the discharge owns every sample until positive
current closes it, including the near-zero rest samples
that taper in at the start and end of the cycle. The
offline loader keeps those, so excluding them here would
shorten discharge_duration_s and drop the tail of the
current trace from the capacity integral.

Rest samples *before* arming are deliberately not claimed.
A charge cycle in this dataset ends with around 100 resting
samples, which is indistinguishable by signal from the two
or three resting samples that begin the next discharge.
Claiming them would add about 300 seconds of rest to the
front of every cycle. The result is that a streamed cycle's
discharge_duration_s runs about 1% short of the .mat value.
That is a floor imposed by signal-only detection, not a
bug to fix by guessing.

Unclosable cycles
-----------------
A discharge ends at the first positive current sample, or
after a sustained rest, whichever comes first.

The rest rule matters because these cells separate some
discharges only with impedance phases, and a device that
streams once a second publishes a near-zero current through
those. Without a rest rule the two discharges merge into one
long cycle with badly wrong duration and capacity. A
sustained rest is treated as a gap between cycles and the
resting samples are excluded from both, so the cycle keeps
its own taper without absorbing the gap.

This dataset cannot exercise the rest rule. The impedance
cycles were stripped before it was distributed, so they carry
zero samples and are invisible to any signal-only detector:
B0005 file cycles 309 and 312 are two discharges separated by
nothing at all, and from the current alone they are one
continuous stretch. So a replay emits 167 of the 168
labelled discharges, and the one it cannot recover is fc=309
merged with fc=312. The offline parser separates them only
because it reads the cycle labels, which a live device does
not have.

The final discharge in a file has no positive current after
it either. flush() emits such a cycle once the caller knows
the stream has ended.

Measured agreement with the offline parser
------------------------------------------
Replaying B0018 end to end and matching each streamed cycle
to the .mat cycle that produced it gives 132 of 132 cycles
and a mean relative difference of 0.01% on voltage_mean,
0.00% on voltage_min, voltage_max, current_min and
discharge_duration_s, 0.15% on voltage_std, 0.20% on
temperature_std and 0.87% on integrated capacity.

Two features do not agree, both for one reason. The last
resting sample before a charge phase and the first resting
sample of that charge phase are both near zero current, so
nothing in the signal says which cycle owns it. Claiming it
puts 0.29 V into voltage_end and takes the same 0.29 V out
of voltage_drop, a mean difference of 2.4% and 10.3% on
B0018. Both alternatives were measured and are worse:
discarding the whole rest taper instead gives voltage_drop
an error of 0.70 V, roughly twice as large, because the
taper is nine samples of genuine recovery. The bias is
constant in sign, so it is a train/serve offset rather than
noise, and it is the one number to watch if streamed
predictions drift from offline ones.

The 'cycle' feature does not agree at all, and cannot
-----------------------------------------------------
The one feature with a genuine train/serve skew is `cycle`.

Offline, `cycle` is the position in the raw .mat cycle array,
counting charge and impedance phases as well as discharges
(load_data.py enumerates the whole array). Streaming, `cycle`
is the number of completed discharges, because that is the
only thing a device can count. Measured on the four cells:

    cell    labelled   offline range   streamed range   mean error
    B0005      168      2..614         1..167           -210
    B0006      168      2..614         1..167           -210
    B0007      168      2..614         1..167           -210
    B0018      132      3..319         1..132            -98

Not one row matches, and the offline column runs 2.6x to
3.2x ahead.

No counting rule on the tracker side can close this gap.
Counting every observable phase gets closer but still misses
badly, because B0005 contains 278 impedance phases and the
ones that are actually empty carry zero samples:

    cell    offline max   observable phases
    B0005         616          338
    B0018         319          266

A device that counts discharges and charges is therefore still
short, and a device that only discharges, which is the common
case for a BMS, is short by more. Making the tracker guess at
impedance phases would invent information the signal does not
contain.

So this is a training-side defect, not a streaming one. `cycle`
is an age proxy, and its scale should be defined once, in a way
both training and serving can reproduce. Two ways to close it,
both needing a retrain:

  1. Define `cycle` in the training set as the discharge
     ordinal, matching the tracker. Keeps the feature and its
     aging signal, and makes the definition servable.
  2. Drop `cycle` from the feature list. Simplest and most
     honest, at the cost of whatever aging signal it carried.

Until one of those is done, treat streamed `cycle` as a
within-stream counter and do not compare it to the training
column. tests/test_replay.py pins the difference so it cannot
be forgotten or drift silently.
"""

from collections import deque

import numpy as np

from pack.resistance import estimate_dcr

try:
    from load_data import (
        MIN_CYCLE_SAMPLES,
        build_cycle_features
    )
except ImportError:
    from ..load_data import (
        MIN_CYCLE_SAMPLES,
        build_cycle_features
    )


# Upper bound on samples held for one cycle, at 1 Hz this is
# six hours. Real discharges here run 2792 to 3690 seconds,
# so this never truncates a healthy cycle. It exists to stop a
# stuck negative-current sensor from growing the buffer
# without limit: on overflow the partial cycle is emitted
# rather than silently truncated, because a truncated cycle
# would corrupt discharge_duration_s and the capacity
# integral while still looking valid.
DEFAULT_MAX_SAMPLES = 21600

# Current magnitude below which a sample counts as resting
# rather than discharging or charging. A threshold rather
# than a strict sign test, because current measurements carry
# small positive noise around a true zero crossing.
IDLE_CURRENT_A = 0.05

# Consecutive discharging samples required before a discharge
# is considered to have started.
DISCHARGE_DEBOUNCE_SAMPLES = 5

# Consecutive resting samples that end a discharge. Each
# discharge in this dataset tapers to rest for up to about
# 15 samples before charging resumes, so this sits well clear
# of a taper. At one sample per second, 300 s is five minutes,
# which is shorter than the impedance phases separating two
# discharges in this protocol and so separates them, while
# being far longer than any taper observed.
IDLE_CLOSE_SAMPLES = 300

# Consecutive positive samples that end a discharge. Every
# charge phase in this dataset opens with one negative glitch
# and then a run of several hundred positive samples, so a
# small value separates them. It exists because closing on the
# first positive sample is not safe: the sample before that
# glitch rests at about zero, so a naive rule absorbs the
# rest and then reads the following negative glitch as a
# discharge resuming, putting a -3.4 A spike into the cycle.
CHARGE_DEBOUNCE_SAMPLES = 3

# Resting samples claimed for the start of a discharge when it
# arms. Every discharge in B0005, B0006 and B0018 begins with
# exactly two resting samples, the moment the load is applied
# after a rest, so this reproduces the .mat cycle boundary
# exactly rather than approximately.
#
# It matters more than the sample count suggests. Those two
# samples sit at the resting voltage near 4.19 V, while the
# first loaded sample is already down near 3.7 V, so omitting
# them shifts voltage_end, voltage_max and voltage_drop: it
# measured a 44% error in voltage_drop before this was added.
# Only resting samples are ever claimed, never charging ones,
# and the preceding rest runs for hundreds of samples, so this
# cannot reach back into real charge data. Two samples is about
# six seconds, so a wrong guess for another device is cheap.
DISCHARGE_LEAD_IN_SAMPLES = 2

# elapsed time into a discharge at which cell voltages are compared
REF_ELAPSED_S = 900.0

CHARGE = "charge"
DISCHARGE = "discharge"
IDLE = "idle"


class CycleTracker:
    """
    Accumulates per-second samples and emits one feature row
    per completed discharge cycle.

    Feed every sample to add_sample(). It returns a feature
    row dict on the sample that closes a discharge, and None
    on every other sample. Call flush() when the stream ends.
    """

    def __init__(
        self,
        cell_id="UNKNOWN",
        max_samples=DEFAULT_MAX_SAMPLES,
        idle_current_a=IDLE_CURRENT_A,
        discharge_debounce=DISCHARGE_DEBOUNCE_SAMPLES,
        idle_close=IDLE_CLOSE_SAMPLES,
        charge_debounce=CHARGE_DEBOUNCE_SAMPLES,
        lead_in=DISCHARGE_LEAD_IN_SAMPLES
    ):

        self.cell_id = cell_id
        self.max_samples = int(max_samples)
        self.idle_current_a = idle_current_a
        self.discharge_debounce = discharge_debounce
        self.idle_close = int(idle_close)
        self.charge_debounce = charge_debounce
        self.lead_in = int(lead_in)

        self._phase = None
        self._cycle_index = 0

        self._last_capacity_ah = np.nan
        self._ambient_temperature = np.nan

        # Highest timestamp accepted so far, and how many samples
        # were thrown away for not advancing it.
        #
        # This is stream-global rather than per-cycle, because a
        # sample arriving out of order across a cycle boundary is
        # just as damaging as one inside a cycle.
        #
        # The reason is the capacity integral, a trapezoidal
        # integral of current over time. A repeated or backwards
        # timestamp gives a zero or negative width between two
        # samples, which either drops real charge out of the
        # integral or counts some twice. Either way capacity_ah
        # comes out wrong for that cycle while every other
        # feature still looks plausible, so the error is hard to
        # notice downstream.
        #
        # Duplicate delivery is not hypothetical: an at-least-
        # once broker may redeliver a QoS 1 message whenever it
        # does not get an acknowledgement in time.
        self._last_timestamp = None
        self._out_of_order = 0

        # A discharge is only armed once enough consecutive
        # discharging samples have been seen to rule out a
        # glitch. Until then a positive sample is just a
        # glitch and must not close anything.
        self._discharge_armed = False
        self._consecutive_discharge = 0
        self._pending_discharge = []

        # Consecutive positive samples seen while armed. A
        # discharge closes only once this clears
        # CHARGE_DEBOUNCE_SAMPLES, mirroring the debounce used
        # to arm it.
        self._charge_run = 0

        # Consecutive resting samples in the armed discharge.
        # Reaching idle_close means the rest is a gap between
        # cycles rather than the taper at the end of one.
        self._consecutive_idle = 0

        # Discharging samples seen while armed but not yet long
        # enough to be trusted. Held so that a single negative
        # glitch, which opens every charge phase in this
        # dataset, is discarded instead of joining the cycle.
        self._unconfirmed_run = []

        # The most recent resting samples seen before a
        # discharge arms. Claimed as the cycle's lead-in so the
        # row starts at rest rather than at the first loaded
        # sample. Resting samples only, so this can never reach
        # back into charging data.
        self._idle_lead = deque(
            maxlen=max(self.lead_in, 1)
        )

    def add_sample(
        self,
        timestamp,
        voltage,
        current,
        temperature,
        ambient_temperature=np.nan
    ):
        """
        Add one per-second sample.

        Returns a feature dict when this sample closes a
        discharge, otherwise None.
        """

        timestamp = float(timestamp)

        # Reject a sample that does not advance the clock before
        # it reaches any of the state machine, the run buffers or
        # the capacity integral. Dropping it here keeps a
        # redelivered message from corrupting the cycle it lands
        # in, and keeps the phase debounce counters honest.
        if (
            self._last_timestamp is not None
            and timestamp <= self._last_timestamp
        ):

            self._out_of_order += 1

            return None

        self._last_timestamp = timestamp

        phase = self._classify(current)

        sample = (
            timestamp,
            float(voltage),
            float(current),
            float(temperature)
        )

        if np.isfinite(ambient_temperature):

            self._ambient_temperature = float(
                ambient_temperature
            )

        # Closing is checked before ownership, so the charge
        # sample that ends a discharge is never counted as
        # part of it.
        if self._discharge_armed and phase == CHARGE:

            self._charge_run += 1

            # Positive current means the negative run that
            # preceded it was a glitch inside the charge phase,
            # so it is discarded rather than committed.
            self._unconfirmed_run = []

            if self._charge_run >= self.charge_debounce:

                self._phase = phase

                return self._finalize()

            # Confirmed charge samples belong to the next
            # cycle and are dropped. Holding the decision for a
            # few samples stops a single positive spike from
            # truncating the discharge, and stops the negative
            # glitch that opens every charge phase from being
            # mistaken for a discharge resuming.
            self._phase = phase

            return None

        self._charge_run = 0

        if self._discharge_armed:

            if phase == IDLE:

                # A rest means the negative run before it was
                # real, so commit it. Those samples are the
                # deepest part of the discharge, and dropping
                # them would bias voltage_min and current_min
                # high.
                self._commit_unconfirmed()

                # Part of this cycle's taper, so it is kept.
                self._consecutive_idle += 1
                self._pending_discharge.append(sample)

                if self._consecutive_idle >= self.idle_close:

                    # A sustained rest. Treat it as the gap
                    # between two cycles: drop the resting
                    # samples so they are counted in neither,
                    # then close.
                    del self._pending_discharge[
                        -self.idle_close:
                    ]

                    self._phase = phase

                    return self._finalize()

            else:

                # Discharging while armed. Either the
                # discharge is genuinely running again, or
                # this is the negative glitch that opens a
                # charge phase. The run is held until what
                # follows it decides which: a rest commits it,
                # positive current discards it.
                self._unconfirmed_run.append(sample)

                if (
                    len(self._unconfirmed_run)
                    >= self.discharge_debounce
                ):

                    self._commit_unconfirmed()

        elif phase == DISCHARGE:

            self._consecutive_discharge += 1
            self._pending_discharge.append(sample)

            if (
                self._consecutive_discharge
                >= self.discharge_debounce
            ):

                self._discharge_armed = True

                # The rest immediately before the load was
                # applied is the start of this cycle.
                self._pending_discharge = (
                    list(self._idle_lead)
                    + self._pending_discharge
                )

                self._idle_lead.clear()

        else:

            # Un-armed and not discharging: a charge glitch
            # or a rest. Drop the window so a negative blip
            # cannot seed a phantom discharge.
            self._consecutive_discharge = 0
            self._pending_discharge = []

            if phase == IDLE:

                self._idle_lead.append(sample)

            else:

                self._idle_lead.clear()

        self._phase = phase

        if (
            self._discharge_armed
            and self.samples_in_discharge
            > self.max_samples
        ):

            return self._finalize()

        return None

    def _commit_unconfirmed(self):
        """
        Move a held negative run into the cycle.

        Called when a rest proves the run was a real discharge
        rather than a glitch opening a charge phase.
        """

        if not self._unconfirmed_run:

            return

        self._pending_discharge.extend(
            self._unconfirmed_run
        )

        self._unconfirmed_run = []
        self._consecutive_idle = 0

    def _classify(self, current):

        if current < -self.idle_current_a:
            return DISCHARGE

        if current > self.idle_current_a:
            return CHARGE

        return IDLE

    def _finalize(self):

        samples = self._pending_discharge

        self._pending_discharge = []
        self._unconfirmed_run = []
        self._discharge_armed = False
        self._consecutive_discharge = 0
        self._charge_run = 0
        self._consecutive_idle = 0

        if len(samples) < MIN_CYCLE_SAMPLES:

            return None

        samples = np.asarray(
            samples,
            dtype=float
        )

        self._cycle_index += 1

        # The .mat files carry a Capacity field measured by the
        # bench. A live device has no such field, so capacity
        # is integrated from the current trace, which is the
        # same quantity the bench measured.
        capacity = self._integrate_capacity(
            samples[:, 0], samples[:, 2]
        )

        row = build_cycle_features(
            voltage=samples[:, 1],
            current=samples[:, 2],
            temperature=samples[:, 3],
            time=samples[:, 0],
            capacity=capacity,
            ambient_temperature=self._ambient_temperature,
            cell_id=self.cell_id,
            cycle_number=self._cycle_index
        )

        if row is None:

            return None

        # Real DC resistance from the load-on step (R = -dV/dI),
        # for pack monitoring. Not a model feature. None when the
        # trace has no usable step.
        dcr = estimate_dcr(
            samples[:, 1],
            samples[:, 2],
            samples[:, 0],
            discharge_positive=False,
            max_dt_s=30.0,
        )
        row["dcr_ohm"] = None if dcr is None else dcr["dcr_ohm"]

        # Voltage a fixed time after the load comes on. Cells in
        # a series pack discharge together, so comparing them at
        # the same instant is what shows imbalance. End voltage
        # is not comparable: the bench stops each cell at its own
        # cutoff (2.7 / 2.2 / 2.5 V for B0005 / B0007 / B0018).
        loaded = np.flatnonzero(np.abs(samples[:, 2]) > 0.2)

        if len(loaded):

            t_ref = samples[loaded[0], 0] + REF_ELAPSED_S

            row["v_ref"] = (
                float(np.interp(t_ref, samples[:, 0], samples[:, 1]))
                if samples[-1, 0] >= t_ref
                else None
            )

        else:

            row["v_ref"] = None

        row["capacity_change_ah"] = (
            capacity - self._last_capacity_ah
        )

        # Derived from the target in training, so it cannot
        # exist for a cell the model has not seen. NaN here
        # lets the fitted imputer substitute the training
        # median, which scored better than the value itself.
        row["soh_change_pct"] = np.nan

        self._last_capacity_ah = capacity

        return row

    @staticmethod
    def _integrate_capacity(timestamps, current):
        """
        Discharge capacity in Ah by trapezoidal integration
        of current over time.

        Integrating amps against seconds gives amp-seconds,
        so the result is divided by 3600 to reach amp-hours.
        This matches the bench Capacity field to within about
        0.3% on this dataset.
        """

        if len(timestamps) < 2:

            return np.nan

        # Discharge current is negative, so the integral is
        # negative. Negate to report a positive quantity,
        # matching the .mat Capacity field.
        amp_seconds = np.trapezoid(
            current, timestamps
        )

        if not np.isfinite(amp_seconds):

            return np.nan

        return float(-amp_seconds / 3600.0)

    def flush(self):
        """
        Emit the armed discharge without waiting for a close.

        Returns a feature row, or None when nothing is armed.
        Call this when the stream ends, or after a quiet gap
        long enough to treat the cycle as finished.
        """

        if not self._discharge_armed:

            return None

        return self._finalize()

    @property
    def cycle_index(self):
        """
        Count of completed discharge cycles.
        """

        return self._cycle_index

    @property
    def phase(self):
        """
        Most recent phase: charge, discharge or idle.
        """

        return self._phase

    @property
    def samples_in_discharge(self):
        """
        Samples accumulated for the armed discharge so far,
        including any run not yet confirmed.
        """

        return (
            len(self._pending_discharge)
            + len(self._unconfirmed_run)
        )

    @property
    def out_of_order_samples(self):
        """
        Samples rejected for not advancing the timestamp.

        A broker on an unreliable link will produce these, so a
        non-zero count is normal operation rather than a fault.
        What matters is that it is visible: a device whose
        capacity is drifting should be able to rule out
        duplicated or reordered telemetry before it suspects the
        model.
        """

        return self._out_of_order

    def reset(self):
        """
        Drop buffered samples and cycle state.
        """

        self._phase = None
        self._cycle_index = 0
        self._last_capacity_ah = np.nan
        self._ambient_temperature = np.nan

        # The timestamp watermark is deliberately not cleared.
        # It describes the stream, not the current cycle, and a
        # reset is usually followed by data older than what has
        # already been seen, which the guard should keep
        # rejecting rather than silently accept.
        self._discharge_armed = False
        self._consecutive_discharge = 0
        self._pending_discharge = []
        self._charge_run = 0
        self._consecutive_idle = 0
        self._unconfirmed_run = []
