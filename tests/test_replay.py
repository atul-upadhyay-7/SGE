"""
test_replay.py
--------------
End-to-end check of the streaming path against the offline
parser, on the real .mat files.

This is the test that matters most. The tracker is a rewrite of
the .mat segmentation as a running state machine, so agreement
with parse_battery_file is the only evidence that a live
device would see the same features the model was trained on.

Skipped when the raw data is absent, so the suite still runs
without it.
"""

import numpy as np
import pytest

from conftest import ROOT, make_bundle

from cycle_tracker import CycleTracker
from load_data import parse_battery_file
from replay import read_cycles, replay_cell
from soh_service import SohService


RAW = ROOT / "BMS Dataset" / "Battery_DataSet" / "Battery_DataSet"


def mat_for(cell):

    return RAW / f"{cell}.mat"


pytestmark = pytest.mark.skipif(
    not RAW.exists(),
    reason="raw NASA .mat data not present"
)


class OriginTracker(CycleTracker):
    """
    Tracker that records which file cycle armed each discharge.

    Needed to match streamed cycles to offline ones. Without it
    a single merged cycle shifts every later row and the
    comparison silently compares the wrong cycles.
    """

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        self.current_fc = None
        self.origin = None
        self._was_armed = False
        self.emitted = []

    def add_sample(self, *args, **kwargs):

        row = super().add_sample(*args, **kwargs)

        if self._discharge_armed and not self._was_armed:

            self.origin = self.current_fc

        self._was_armed = self._discharge_armed

        if row is not None:

            self.emitted.append((self.origin, row))

        return row

    def flush(self):

        # flush() finalizes without going through add_sample, so
        # it has to be recorded separately or the last cycle of
        # every file goes uncounted.
        row = super().flush()

        if row is not None:

            self.emitted.append((self.origin, row))

        return row


def stream_cell(mat_path, cell_id):
    """
    Replay a cell through the tracker only.

    Returns the tracker; emitted is a list of
    (file cycle index, feature row) in emission order.
    """

    tracker = OriginTracker(cell_id=cell_id)

    clock = 0.0

    for index, cycle in enumerate(read_cycles(mat_path)):

        timestamps = cycle["time"]

        if len(timestamps) == 0:

            continue

        tracker.current_fc = index

        offsets = np.concatenate(
            ([0.0], np.cumsum(np.diff(timestamps)))
        )

        start = clock

        for i in range(len(timestamps)):

            tracker.add_sample(
                start + float(offsets[i]),
                cycle["voltage"][i],
                cycle["current"][i],
                cycle["temperature"][i],
                cycle["ambient"]
            )

        clock = start + float(offsets[-1])

    # The final discharge has no positive current after it, so it
    # is only emitted once the stream ends. Its origin is still
    # the file cycle that armed it, so clearing current_fc here
    # would only discard the match.
    tracker.flush()

    return tracker


@pytest.mark.parametrize("cell", ["B0018"])
def test_every_labelled_discharge_is_recovered(cell):

    mat = mat_for(cell)

    if not mat.exists():

        pytest.skip(f"{cell}.mat not present")

    cycles = list(read_cycles(mat))

    labelled = [
        i for i, c in enumerate(cycles)
        if c["phase"] == "discharge" and len(c["time"]) > 0
    ]

    tracker = stream_cell(mat, cell)

    assert tracker.cycle_index == len(labelled), (
        f"recovered {tracker.cycle_index} of "
        f"{len(labelled)} labelled discharges"
    )


@pytest.mark.parametrize("cell", ["B0005", "B0018"])
def test_features_agree_with_the_offline_parser(cell):

    mat = mat_for(cell)

    if not mat.exists():

        pytest.skip(f"{cell}.mat not present")

    truth = parse_battery_file(mat).sort_values(
        "cycle"
    ).reset_index(drop=True)

    cycles = list(read_cycles(mat))

    labelled = [
        i for i, c in enumerate(cycles)
        if c["phase"] == "discharge" and len(c["time"]) > 0
    ]

    fc_to_row = {
        fc: i for i, fc in enumerate(labelled)
    }

    tracker = stream_cell(mat, cell)

    matched = [
        (fc_to_row[fc], row)
        for fc, row in tracker.emitted
        if fc in fc_to_row
    ]

    # B0005 merges one labelled pair, because the only thing
    # between file cycles 309 and 312 is an impedance phase with
    # no samples in it, so the current signal is one continuous
    # negative stretch. The offline parser separates them only
    # because it reads the cycle labels.
    assert len(matched) >= len(labelled) - 1

    # Tolerances are the measured mean differences on B0018.
    # voltage_end and voltage_drop are excluded deliberately:
    # one resting sample cannot be attributed to either cycle,
    # which biases voltage_end by 0.29 V in a constant
    # direction. See the note in cycle_tracker's docstring.
    tolerances = {
        "voltage_mean": 0.01,
        "voltage_min": 0.01,
        "voltage_max": 0.01,
        "voltage_std": 0.02,
        "current_mean": 0.01,
        "current_min": 0.01,
        "temperature_mean": 0.01,
        "temperature_max": 0.01,
        "discharge_duration_s": 0.01,
        "capacity_ah": 0.02,
        "resistance_proxy_ohm": 0.02,
    }

    for feature, tolerance in tolerances.items():

        streamed = np.array(
            [r[feature] for _, r in matched], float
        )
        offline = np.array(
            [truth[feature].values[i] for i, _ in matched],
            float
        )

        scale = np.abs(offline).mean()
        rel = np.abs(streamed - offline).mean() / scale

        assert rel < tolerance, (
            f"{cell} {feature}: mean relative difference "
            f"{rel * 100:.2f}% exceeds {tolerance * 100:.0f}%"
        )


def test_the_glitch_never_enters_a_cycle():

    mat = mat_for("B0018")

    if not mat.exists():

        pytest.skip("B0018.mat not present")

    tracker = stream_cell(mat, "B0018")

    assert tracker.emitted

    worst = max(
        abs(row["current_min"])
        for _, row in tracker.emitted
    )

    # Every real discharge bottoms out near -2.0 A. The negative
    # glitch that opens a charge phase reaches -4.5 A, so a cycle
    # carrying one is unmistakably wrong. Before the run buffer
    # was cleared on a rest, 23 of 132 cycles carried one.
    assert worst < 2.5, (
        f"a charge-phase glitch reached {worst:.3f} A, so the "
        f"negative-run debounce is not holding"
    )


def test_the_cycle_feature_is_a_known_train_serve_skew():

    mat = mat_for("B0018")

    if not mat.exists():

        pytest.skip("B0018.mat not present")

    truth = parse_battery_file(mat).sort_values(
        "cycle"
    ).reset_index(drop=True)

    cycles = list(read_cycles(mat))

    labelled = [
        i for i, c in enumerate(cycles)
        if c["phase"] == "discharge" and len(c["time"]) > 0
    ]

    fc_to_row = {fc: i for i, fc in enumerate(labelled)}

    tracker = stream_cell(mat, "B0018")

    streamed = [
        row["cycle"]
        for fc, row in tracker.emitted
        if fc in fc_to_row
    ]
    offline = [
        int(truth["cycle"].values[fc_to_row[fc]])
        for fc, _ in tracker.emitted
        if fc in fc_to_row
    ]

    # Offline `cycle` is the raw .mat array position, so it
    # counts charge and impedance phases too. Streaming counts
    # completed discharges. They do not match today, and this
    # test exists to make that failure loud if anyone assumes
    # otherwise.
    assert streamed == list(range(1, len(labelled) + 1))
    assert offline != streamed
    assert min(offline) > 1

    # The offline column runs well ahead of anything a device
    # can count.
    assert max(offline) > 2 * max(streamed)

    # See the "The 'cycle' feature does not agree at all"
    # section of cycle_tracker's docstring. The fix is to
    # redefine or drop the feature and retrain, not to make the
    # tracker count impedance phases it cannot see.


def test_replay_cell_runs_the_whole_path():

    mat = mat_for("B0018")

    if not mat.exists():

        pytest.skip("B0018.mat not present")

    service = SohService(
        bundle=make_bundle(), cell_id="B0018"
    )

    outcome = replay_cell(
        mat, service=service, max_cycles=40
    )

    assert outcome["samples"] > 0
    assert outcome["wall_time_s"] > 0
    # The model is a constant here, so the first cycle must
    # already have produced a reading.
    assert len(outcome["predictions"]) >= 1
    assert outcome["predictions"][0]["soh"] is not None
    assert outcome["predictions"][-1]["cell_id"] == "B0018"


def test_replay_module_imports_as_a_package():

    # The streaming modules import each other by bare name when
    # run as a script and relatively when imported as a package.
    # Both paths have to work.
    import importlib

    module = importlib.import_module(
        "src.streaming.replay"
    )

    assert hasattr(module, "replay_cell")
    assert hasattr(module, "read_cycles")
