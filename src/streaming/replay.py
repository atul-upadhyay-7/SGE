"""
replay.py
---------
Feed a .mat cell through the live streaming path at
accelerated speed.

This exists so the streaming logic can be exercised, and
timed, without a device or a broker. The samples come from
the same .mat files the offline pipeline reads, and they go
through CycleTracker and SohService exactly as an MQTT
message would, so a passing replay is evidence about the live
path rather than about a test double.

Charge cycles are included deliberately. The tracker only
emits a feature row when a discharge closes, so the charge
cycles exercise the phase transition that closes it. A
discharge-only replay would never close a single cycle.

Timestamps come from the .mat Time vectors rather than a
synthetic one-sample-per-second clock. This dataset is
sampled irregularly, between roughly 2.5 and 22 seconds
apart, so assuming 1 s steps would inflate integrated
capacity by about three times and silently corrupt every
feature downstream. Each Time vector also starts near zero,
so elapsed time is accumulated from the differences between
consecutive samples rather than from the absolute values.
"""

import sys
import time
from pathlib import Path

# Put this directory and its parent on sys.path before any
# project import, so the module works both as a script and
# when imported as src.streaming.replay.
sys.path.insert(
    0, str(Path(__file__).resolve().parent)
)
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent)
)

import numpy as np
from scipy.io import loadmat

try:
    from soh_service import SohService
except ImportError:
    from .soh_service import SohService


def read_cycles(
    mat_path,
    max_cycles=None
):
    """
    Yield every cycle from a .mat file as per-sample arrays
    plus its phase label.
    """

    mat_path = Path(mat_path)

    mat = loadmat(
        mat_path,
        squeeze_me=True,
        struct_as_record=False
    )

    cell_id = mat_path.stem.upper()

    battery = None

    if cell_id in mat:

        battery = mat[cell_id]

    else:

        candidates = [
            key for key in mat
            if not key.startswith("__")
        ]

        battery = mat[candidates[0]]

    cycles = np.atleast_1d(
        getattr(battery, "cycle", None)
    )

    emitted = 0

    for index, cycle in enumerate(cycles):

        if (
            max_cycles is not None
            and emitted >= max_cycles
        ):

            return

        phase = str(
            getattr(cycle, "type", "")
        ).lower()

        data = getattr(cycle, "data", None)

        if data is None:

            continue

        voltage = np.atleast_1d(
            getattr(
                data, "Voltage_measured", []
            )
        ).astype(float)

        current = np.atleast_1d(
            getattr(
                data, "Current_measured", []
            )
        ).astype(float)

        temperature = np.atleast_1d(
            getattr(
                data, "Temperature_measured", []
            )
        ).astype(float)

        timestamps = np.atleast_1d(
            getattr(data, "Time", [])
        ).astype(float)

        ambient = getattr(
            cycle, "ambient_temperature", np.nan
        )

        ambient = float(
            np.atleast_1d(ambient)[0]
        ) if np.size(ambient) else np.nan

        yield {
            "phase": phase,
            "voltage": voltage,
            "current": current,
            "temperature": temperature,
            "time": timestamps,
            "ambient": ambient
        }

        emitted += 1


def replay_cell(
    mat_path,
    service=None,
    max_cycles=None,
    on_prediction=None
):
    """
    Push a cell's samples through a service in order and
    return the predictions.

    service: an SohService. When None one is built, which
    requires a loadable model.

    on_prediction: optional callback invoked with each
    prediction dict, for live progress output.
    """

    if service is None:

        service = SohService(
            cell_id=Path(mat_path).stem.upper()
        )

    predictions = []

    # A monotonic clock accumulated from the real sample
    # intervals in the .mat Time vectors, not a synthetic
    # one-sample-per-second step. This dataset is sampled
    # irregularly between roughly 2.5 and 22 seconds apart, so
    # assuming 1 s steps would inflate integrated capacity by
    # about three times and silently corrupt every feature
    # downstream.
    clock = 0.0

    started = time.perf_counter()

    sample_count = 0

    for cycle in read_cycles(
        mat_path, max_cycles
    ):

        timestamps = cycle["time"]

        if len(timestamps) == 0:

            continue

        # Elapsed seconds within this cycle, accumulated from
        # the differences between consecutive samples. Each
        # Time vector starts near zero, so the cumulative
        # offset is the correct per-sample clock, and the
        # running clock simply resumes from where the previous
        # cycle ended.
        #
        # Note the offset is used as-is, not added to the
        # clock. Adding a cumulative offset each iteration
        # would integrate the whole elapsed span once per
        # sample and inflate a 3000 second cycle to billions.
        offsets = np.concatenate(
            (
                [0.0],
                np.cumsum(
                    np.diff(timestamps)
                )
            )
        )

        cycle_start = clock

        for index in range(len(timestamps)):

            result = service.add_sample(
                timestamp=(
                    cycle_start
                    + float(offsets[index])
                ),
                voltage=cycle["voltage"][index],
                current=cycle["current"][index],
                temperature=cycle["temperature"][index],
                ambient_temperature=cycle["ambient"]
            )

            sample_count += 1

            if result is None:

                continue

            predictions.append(result)

            if on_prediction is not None:

                on_prediction(result)

        # Next cycle begins after this one ends.
        if len(offsets) > 0:

            clock = cycle_start + float(offsets[-1])

    # The final discharge in a file has no positive current
    # after it, so it can only be emitted once the stream has
    # ended. This is why flush() exists, and why a replay that
    # never flushes looks like it lost its last cycle.
    #
    # flush() does not recover B0005's file cycles 309 and 312.
    # Those two are separated by impedance cycles that contain
    # no samples at all, so the current signal across them is
    # one continuous negative stretch and they close as a single
    # merged cycle. The offline parser splits them only because
    # it reads the cycle labels, which a device never sees.
    tail = service.flush()

    if tail is not None:

        predictions.append(tail)

        if on_prediction is not None:

            on_prediction(tail)

    elapsed = time.perf_counter() - started

    return {
        "predictions": predictions,
        "wall_time_s": elapsed,
        "samples": sample_count,
        "duration_s": clock,
        "service": service
    }


def replay_path(raw_dir, cell, max_cycles=None):
    """
    Replay one named cell from a raw directory.
    """

    raw_dir = Path(raw_dir)

    return replay_cell(
        raw_dir / f"{cell}.mat",
        max_cycles=max_cycles
    )


def main():

    import argparse

    from load_data import resolve_raw_dir

    parser = argparse.ArgumentParser(
        description=(
            "Replay a NASA cell through the streaming SOH "
            "path."
        )
    )

    parser.add_argument(
        "--cell", default="B0005"
    )

    parser.add_argument(
        "--raw-dir", default=None
    )

    parser.add_argument(
        "--max-cycles", type=int, default=None
    )

    args = parser.parse_args()

    raw_dir = (
        Path(args.raw_dir)
        if args.raw_dir
        else resolve_raw_dir()
    )

    def report(result):

        print(
            f"cycle {result['cycle']:>4}  "
            f"soh {result['soh']:6.2f}  "
            f"predict {result['predict_duration_ms']:6.3f} ms"
        )

    outcome = replay_path(
        raw_dir,
        args.cell,
        max_cycles=args.max_cycles
    )

    print(
        f"\nReplayed {outcome['samples']} samples in "
        f"{outcome['wall_time_s']:.2f} s wall "
        f"(accelerated)"
    )

    print(
        f"Cycles predicted: "
        f"{len(outcome['predictions'])}"
    )

    if not outcome["predictions"]:

        print(
            "No predictions. A cell needs at least one "
            "complete discharge cycle to produce a reading."
        )

        return

    final = outcome["predictions"][-1]

    print(
        f"\nFinal latched SOH: {final['soh']:.2f}% "
        f"(cycle {final['cycle']})"
    )

    durations = [
        p["predict_duration_ms"]
        for p in outcome["predictions"]
    ]

    print(
        f"Predict duration: max "
        f"{max(durations):.3f} ms, mean "
        f"{sum(durations) / len(durations):.3f} ms"
    )


if __name__ == "__main__":

    # sys.path is already set up at module import above, so the
    # bare "load_data" import inside main() resolves.
    main()
