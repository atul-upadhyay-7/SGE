"""
bridge.py
---------
Joins the per-cell SOH streams into one pack view.

Each cell has its own MqttSohClient (bms/<cell>/telemetry in,
bms/<cell>/soh out). This bridge subscribes to bms/+/soh, feeds a
PackMonitor whenever a cell reports a new discharge, and

  * publishes the pack state, retained, on bms/pack/<pack>/state
  * writes it to a small SQLite file the Grafana API reads
    (data/live_pack.db), so the dashboard shows the live pack

The logic is in PackBridge.on_soh() and has no network
dependency, so it is tested without a broker. main() only wires
paho to it.
"""

import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pack.monitor import PackMonitor, load_iforest  # noqa: E402
from rul_trajectory import TrajectoryRul  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
LIVE_DB = ROOT / "data" / "live_pack.db"
IFOREST_PATH = ROOT / "models" / "pack_iforest.joblib"
RUL_PATH = ROOT / "models" / "rul_reference_curves.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_pack_cells (
    cell_id TEXT PRIMARY KEY,
    discharge_cycle INTEGER,
    soh_pct REAL,
    resistance_ohm REAL,
    resistance_ratio REAL,
    v_end_v REAL,
    temp_max_c REAL,
    fade_pct_per_cycle REAL,
    cycles_to_retirement INTEGER,
    anomaly_score REAL,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS live_pack_history (
    discharge_cycle INTEGER,
    cell_id TEXT,
    soh_pct REAL,
    v_end_v REAL,
    resistance_ohm REAL,
    temp_max_c REAL,
    voltage_spread_mv REAL,
    soh_spread_pct REAL,
    UNIQUE (cell_id, discharge_cycle)
);
CREATE TABLE IF NOT EXISTS live_pack_alerts (
    severity TEXT,
    code TEXT,
    cell_id TEXT,
    message TEXT,
    value REAL,
    threshold REAL
);
CREATE TABLE IF NOT EXISTS live_pack_status (
    pack_id TEXT PRIMARY KEY,
    maintenance TEXT,
    voltage_spread_mv REAL,
    soh_spread_pct REAL,
    weakest_cell TEXT,
    alert_count INTEGER,
    updated_at REAL
);
"""


class PackBridge:

    def __init__(
        self,
        pack_id,
        cell_ids,
        db_path=LIVE_DB,
        publish=None,
        iforest=None,
        monitor=None,
        rul=None,
    ):

        self.pack_id = pack_id
        self.cell_ids = list(cell_ids)
        self.monitor = monitor or PackMonitor(
            self.cell_ids, iforest=iforest, rul=rul
        )
        self.publish = publish
        self._seen = {}
        self.db_path = Path(db_path) if db_path else None
        self.state = None

        if self.db_path is not None:

            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path)
            conn.executescript(SCHEMA)
            for table in (
                "live_pack_cells",
                "live_pack_history",
                "live_pack_alerts",
                "live_pack_status",
            ):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
            conn.close()

    def on_soh(self, cell_id, payload):
        """
        Handle one bms/<cell>/soh message (a dict). Returns the
        new pack state when this message carried a new discharge,
        otherwise None.
        """

        if cell_id not in self.monitor.cells:

            return None

        last = payload.get("last_cycle") or {}
        seen = payload.get("cycles_seen")

        if (
            payload.get("soh") is None
            or seen is None
            or not last
            or self._seen.get(cell_id) == seen
        ):

            return None

        self._seen[cell_id] = seen

        self.monitor.update(
            cell_id,
            {
                "cycle": last.get("discharge_index") or seen,
                "soh": payload["soh"],
                "v_end": last.get("v_ref") or last.get("v_end"),
                "resistance_ohm": last.get("resistance_ohm"),
                "temp_max": last.get("temp_max"),
                "load_a": last.get("load_a"),
            },
        )

        report = self.monitor.evaluate()
        report["pack_id"] = self.pack_id
        report["updated_at"] = time.time()
        self.state = report

        self._write(report)

        if self.publish is not None:

            self.publish(
                f"bms/pack/{self.pack_id}/state",
                json.dumps(report, default=str),
            )

        return report

    def _write(self, report):

        if self.db_path is None:

            return

        pack = report["pack"]
        spread_mv = (
            None
            if pack["voltage_spread_v"] is None
            else 1000.0 * pack["voltage_spread_v"]
        )

        conn = sqlite3.connect(self.db_path, timeout=10)

        try:

            for cell, c in report["cells"].items():

                conn.execute(
                    "INSERT OR REPLACE INTO live_pack_cells "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        cell,
                        c["cycle"],
                        c["soh_pct"],
                        c["resistance_ohm"],
                        c["resistance_ratio"],
                        c["v_end"],
                        c["temp_max_c"],
                        c["fade_pct_per_cycle"],
                        c["cycles_to_retirement"],
                        c.get("anomaly_score"),
                        report["updated_at"],
                    ),
                )

                conn.execute(
                    "INSERT OR REPLACE INTO live_pack_history "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        c["cycle"],
                        cell,
                        c["soh_pct"],
                        c["v_end"],
                        c["resistance_ohm"],
                        c["temp_max_c"],
                        spread_mv,
                        pack["soh_spread_pct"],
                    ),
                )

            conn.execute("DELETE FROM live_pack_alerts")

            for a in report["alerts"]:

                value = a["value"]

                conn.execute(
                    "INSERT INTO live_pack_alerts "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        a["severity"],
                        a["code"],
                        a["cell"],
                        a["message"],
                        None if value is None else float(value),
                        float(a["threshold"]),
                    ),
                )

            conn.execute(
                "INSERT OR REPLACE INTO live_pack_status "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    self.pack_id,
                    report["maintenance"],
                    spread_mv,
                    pack["soh_spread_pct"],
                    pack["weakest_cell"],
                    len(report["alerts"]),
                    report["updated_at"],
                ),
            )

            conn.commit()

        finally:

            conn.close()


def main(argv=None):

    import argparse

    import paho.mqtt.client as mqtt

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--pack", default="PACK1")
    ap.add_argument(
        "--cells", default="CELL1,CELL2,CELL3",
        help="comma-separated cell ids as used in bms/<cell>/soh",
    )
    args = ap.parse_args(argv)

    cells = [c.strip() for c in args.cells.split(",") if c.strip()]

    bridge = PackBridge(
        args.pack,
        cells,
        iforest=load_iforest(IFOREST_PATH),
        rul=TrajectoryRul.load(RUL_PATH),
    )

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"pack-bridge-{args.pack}",
    )

    def on_connect(c, userdata, flags, reason, props=None):
        c.subscribe("bms/+/soh", qos=0)

    def on_message(c, userdata, msg):
        parts = msg.topic.split("/")
        if len(parts) != 3 or parts[1] == "pack":
            return
        try:
            payload = json.loads(msg.payload)
        except ValueError:
            return
        bridge.publish = lambda t, b: c.publish(
            t, b, qos=0, retain=True
        )
        bridge.on_soh(parts[1], payload)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, args.port)
    client.loop_forever()


if __name__ == "__main__":
    main()
