"""
publisher.py
------------
Stand-in for the ESP32: publishes pack telemetry over MQTT.

Replays three NASA cells as the three cells of a series pack, one
telemetry stream per cell on bms/<CELL>/telemetry, in the same
JSON the ESP32 firmware sends:

    {"timestamp": s, "voltage": V, "current": A, "temperature": C}

Current uses the NASA sign convention the model was trained on:
negative while the cell discharges. (The INA219 reports discharge
as positive, so the firmware negates it. See firmware/README.md.)

Cells advance in lockstep, one discharge at a time, so the pack
monitor compares cells at the same discharge number.

--fault injects a named synthetic disturbance into one cell from
a given discharge onward: CELL:KIND:START, e.g.
CELL2:cell_sag:60. Kinds: resistance_spike, cell_sag, thermal.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "streaming")
)

from replay import read_cycles  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
RAW = ROOT / "BMS Dataset" / "Battery_DataSet" / "Battery_DataSet"

DEFAULT_MAP = {
    "CELL1": "B0005",
    "CELL2": "B0007",
    "CELL3": "B0018",
}


def segments(mat_path):
    """
    Yield lists of raw cycles, each ending with one discharge.
    """

    group = []

    for cycle in read_cycles(mat_path):

        if len(cycle["time"]) == 0:

            continue

        group.append(cycle)

        if "discharge" in cycle["phase"]:

            yield group
            group = []


def disturb(cycle, kind, k):
    """
    Apply a synthetic fault to one raw cycle (k = discharges
    since the fault started, 1-based).
    """

    c = dict(cycle)
    v = c["voltage"].copy()
    t = c["temperature"].copy()

    if "discharge" in c["phase"]:

        load = np.abs(c["current"])

        if kind == "resistance_spike":

            v = v - 0.08 * load

        elif kind == "cell_sag":

            v = v - 0.12

        elif kind == "thermal":

            t = t + min(2.0 * k, 35.0)

    c["voltage"], c["temperature"] = v, t

    return c


def publish_group(client, topic, group, clock, qos=1):

    for cycle in group:

        ts = cycle["time"]
        offsets = np.concatenate(([0.0], np.cumsum(np.diff(ts))))

        for i in range(len(ts)):

            body = json.dumps(
                {
                    "timestamp": clock + float(offsets[i]),
                    "voltage": float(cycle["voltage"][i]),
                    "current": float(cycle["current"][i]),
                    "temperature": float(cycle["temperature"][i]),
                }
            )

            client.publish(topic, body, qos=qos)

        clock += float(offsets[-1])

    return clock


def main(argv=None):

    import paho.mqtt.client as mqtt

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--max-discharges", type=int, default=None)
    ap.add_argument(
        "--delay", type=float, default=0.0,
        help="seconds to wait between discharges (pace for a demo)",
    )
    ap.add_argument(
        "--fault", action="append", default=[],
        help="CELL:KIND:START_DISCHARGE (synthetic), repeatable",
    )
    args = ap.parse_args(argv)

    faults = {}

    for spec in args.fault:

        cell, kind, start = spec.split(":")
        faults[cell] = (kind, int(start))

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="pack-publisher",
    )
    client.connect(args.host, args.port)
    client.loop_start()

    gens = {
        cell: segments(RAW / f"{mat}.mat")
        for cell, mat in DEFAULT_MAP.items()
    }
    clocks = {cell: 0.0 for cell in gens}
    active = set(gens)
    n = 0

    while active:

        n += 1

        if args.max_discharges and n > args.max_discharges:

            break

        for cell in list(active):

            try:

                group = next(gens[cell])

            except StopIteration:

                active.discard(cell)
                continue

            if cell in faults and n >= faults[cell][1]:

                kind, start = faults[cell]
                group = [
                    disturb(c, kind, n - start + 1) for c in group
                ]

            clocks[cell] = publish_group(
                client, f"bms/{cell}/telemetry", group, clocks[cell]
            )

        print(f"discharge {n} published", flush=True)

        if args.delay:

            time.sleep(args.delay)

    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
