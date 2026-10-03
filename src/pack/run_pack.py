"""
run_pack.py
-----------
One process, three SOH services: CELL1, CELL2, CELL3.

Each cell gets its own MqttSohClient (bms/<cell>/telemetry in,
bms/<cell>/soh out). Run src/pack/bridge.py beside it to turn the
three SOH streams into a pack view.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "streaming")
)

from mqtt_client import MqttConfig, MqttSohClient  # noqa: E402


def main(argv=None):

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--cells", default="CELL1,CELL2,CELL3")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO)

    clients = [
        MqttSohClient(
            MqttConfig(
                cell_id=c, host=args.host, port=args.port
            )
        )
        for c in args.cells.split(",")
    ]

    for c in clients:

        c.start()

    try:

        while True:

            time.sleep(1.0)

    except KeyboardInterrupt:

        pass

    finally:

        for c in clients:

            c.stop()


if __name__ == "__main__":
    main()
