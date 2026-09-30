"""
soh_service.py
--------------
Hold the latest SOH prediction and serve it on demand.

Timing
------
SOH is predicted once per completed cycle, roughly every
3000 seconds on this data, and read every second. Predicting
per second would be wrong rather than merely wasteful: SOH
moves about 6e-5 percentage points per second here, some
50000x below the model's 3.23 point cross-cell error, and six
of the model's features do not exist until a cycle ends.

So the two rates are separated. add_sample() does the
per-second work, which is a list append and a sign test.
predict_cycle() runs once per cycle, holding a lock for about
2.5 ms. read() returns a memory read.

The model is loaded once when SohService is constructed,
never per call. A cold load takes about 1.7 seconds, longer
than the whole one-second budget, so a service that reloads
per request cannot meet its own deadline.

Safety
------
Until the first cycle completes there is no prediction.
status is WARMING_UP and soh is None. A plausible-looking
number is never invented, because a device acting on a
fabricated SOH is worse off than one that knows there is no
reading yet.

Once a value exists it is latched, but a latch with no
expiry is a silent failure mode: if the stream stops, or
cycles stop closing, the device would keep acting on a
number that no longer reflects the battery. read() therefore
reports STALE once the value is older than stale_after_s.
"""

import threading
import time

import numpy as np
import pandas as pd

try:
    from cycle_tracker import CycleTracker
    from predict import (
        SohModelLoadError,
        load_soh_model,
        predict_soh
    )
except ImportError:
    from .cycle_tracker import CycleTracker
    from ..predict import (
        SohModelLoadError,
        load_soh_model,
        predict_soh
    )


WARMING_UP = "warming_up"
OK = "ok"
STALE = "stale"
FAILED = "failed"

# How long a latched value stays trustworthy without a new
# cycle. Discharges here run up to 3690 s, so this is roughly
# two long cycles. A device feeding a fleet should set it from
# its own expected cycle time: too short and every cycle is
# reported stale, too long and a stopped stream goes unnoticed.
DEFAULT_STALE_AFTER_S = 7200.0


class SohService:
    """
    Per-cycle SOH prediction with a latched read value.
    """

    def __init__(
        self,
        bundle=None,
        cell_id="UNKNOWN",
        max_samples=None,
        predict_timeout_s=None,
        stale_after_s=DEFAULT_STALE_AFTER_S
    ):
        """
        bundle: a loaded model bundle. When None the model is
        loaded here, once, at construction.

        stale_after_s: age at which a latched value stops
        being reported as ok. None disables the check.
        """

        if bundle is None:

            bundle = load_soh_model()

        self.bundle = bundle
        self.features = list(bundle["features"])
        self.pipeline = bundle["model"]

        tracker_kwargs = {}

        if max_samples is not None:

            tracker_kwargs["max_samples"] = max_samples

        self.tracker = CycleTracker(
            cell_id=cell_id,
            **tracker_kwargs
        )

        self.stale_after_s = stale_after_s

        self._lock = threading.Lock()

        self._soh = None
        self._cycle = None
        self._predicted_at = None
        self._predict_duration_s = None
        self._status = WARMING_UP
        self._error = None
        self._cycles_seen = 0

    def add_sample(
        self,
        timestamp,
        voltage,
        current,
        temperature,
        ambient_temperature=None
    ):
        """
        Feed one per-second sample.

        Returns the prediction dict when this sample closed a
        cycle, otherwise None. Cheap enough to call every
        second.
        """

        kwargs = {}

        if ambient_temperature is not None:

            kwargs["ambient_temperature"] = (
                ambient_temperature
            )

        row = self.tracker.add_sample(
            timestamp=timestamp,
            voltage=voltage,
            current=current,
            temperature=temperature,
            **kwargs
        )

        if row is None:

            return None

        return self.predict_cycle(row)

    def flush(self):
        """
        Emit a discharge that will never close on its own.

        A discharge followed only by impedance phases, or
        being the last thing a device reports, has no positive
        current to end it. Call this when the stream ends.
        """

        row = self.tracker.flush()

        if row is None:

            return None

        return self.predict_cycle(row)

    def predict_cycle(self, row):
        """
        Predict SOH for one completed cycle and latch it.

        Also the path used to score a single cycle without a
        stream, which is what replay and the file-based tests
        exercise.
        """

        frame = pd.DataFrame([row])

        started = time.perf_counter()

        try:

            value = predict_soh(
                self.bundle, frame
            )

        except Exception as error:

            # A failed prediction must not overwrite a good
            # latched value, but it must be visible. Status
            # goes to failed and the previous SOH is kept,
            # because a stale reading is more useful to a
            # device than none, and error explains why.
            with self._lock:

                self._status = FAILED
                self._error = (
                    f"{type(error).__name__}: {error}"
                )

            raise

        finally:

            elapsed = (
                time.perf_counter() - started
            )

        if not np.isfinite(value):

            raise ValueError(
                f"Model returned non-finite SOH: {value}"
            )

        with self._lock:

            self._soh = value
            self._cycle = row.get("cycle")
            self._predicted_at = time.time()
            self._predict_duration_s = elapsed
            self._status = OK
            self._error = None
            self._cycles_seen += 1

        return self.read()

    def read(self):
        """
        Current latched SOH.

        Returns None for soh until the first cycle completes,
        and reports status as stale once the value is older
        than stale_after_s, so a device can tell a live
        reading from a leftover one.
        """

        with self._lock:

            status = self._status
            age_s = None

            if self._predicted_at is not None:

                age_s = time.time() - self._predicted_at

                if (
                    status == OK
                    and self.stale_after_s is not None
                    and age_s > self.stale_after_s
                ):

                    status = STALE

            return {
                "soh": self._soh,
                "status": status,
                "cell_id": (
                    self.tracker.cell_id
                ),
                "cycle": self._cycle,
                "phase": self.tracker.phase,
                "cycles_seen": self._cycles_seen,
                "predicted_at": self._predicted_at,
                "age_s": (
                    None
                    if age_s is None
                    else round(age_s, 1)
                ),
                "predict_duration_ms": (
                    None
                    if self._predict_duration_s is None
                    else round(
                        self._predict_duration_s * 1000, 3
                    )
                ),
                "error": self._error
            }

    def warm_up_check(self):
        """
        Assert the service has produced a real reading.

        Raises RuntimeError when no cycle has completed yet, or
        when a prediction failed. This is a post-first-cycle
        assertion, not a boot probe: before any cycle arrives
        there is deliberately nothing to check, so it will
        always raise on a freshly constructed service. Use
        check_model_available() to verify the model loads.
        """

        if self._status == FAILED:

            raise RuntimeError(
                "SOH service is in a failed state: "
                f"{self._error}"
            )

        if self._soh is None:

            raise RuntimeError(
                "SOH service has no prediction yet. At "
                "least one complete discharge cycle is "
                "required before SOH is defined."
            )

        return True


def check_model_available(path=None):
    """
    Load the model and report whether inference is possible.

    Intended for a startup probe or a CI check, so that the
    corrupt-artifact failure surfaces as a clear message
    rather than as a traceback from inside a sample loop.
    """

    try:

        bundle = load_soh_model(path)

    except SohModelLoadError as error:

        return False, str(error)

    return True, (
        f"model ready with {len(bundle['features'])} "
        f"features, trained on "
        f"{', '.join(map(str, bundle.get('trained_on', [])))}"
    )
