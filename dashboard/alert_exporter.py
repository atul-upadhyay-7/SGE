"""
alert_exporter.py
-----------------
Prometheus exporter for the live pack, so Grafana's own alerting
engine can evaluate the battery rules.

Why this exists
---------------
The dashboard panels use simpod-json-datasource. Its plugin.json
declares "alerting": false and "backend": false, so Grafana cannot
run alert rules against it ([plugin.unavailable]); a rule that can
never be evaluated is not an alert. Grafana's built-in Prometheus
datasource is a backend datasource with full alerting support, so
the alert path goes through Prometheus and this exporter instead.

The exporter reads the same SQLite file the JSON API reads
(data/live_pack.db, written by src/pack/bridge.py) and serves the
latest value per cell as Prometheus gauges on /metrics:

    battery_pdm_cell_soh_percent{cell="CELL1"} 70.43
    battery_pdm_cell_peak_temperature_celsius{cell="CELL1"} 41.0
    battery_pdm_cell_resistance_ohm{cell="CELL1"} 0.021

Grafana's Prometheus datasource scrapes this, and the provisioned
rules query it directly. Only the standard library is used, so no
new dependency is pulled into the image.

Usage
  python dashboard/alert_exporter.py                 # 0.0.0.0:9101
  PDM_EXPORTER_PORT=9101 python dashboard/alert_exporter.py
  PDM_LIVE_DB=/path/to/live_pack.db python dashboard/alert_exporter.py
"""

import math
import os
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DB = ROOT / "data" / "live_pack.db"
DEFAULT_PORT = 9101
# Loopback by default, like grafana_api.py: the metrics are unauthenticated.
# docker-compose sets PDM_EXPORTER_HOST=0.0.0.0 inside the compose network
# and publishes the port on 127.0.0.1 only.
DEFAULT_HOST = "127.0.0.1"


def _escape(value):
    """Escape a Prometheus label value (backslash, quote, newline)."""

    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def _number(value):
    """Format a metric value, or None when it is missing/not finite."""

    if value is None:

        return None

    try:

        number = float(value)

    except (TypeError, ValueError):

        return None

    # NaN and ±inf are all invalid in Prometheus exposition format;
    # repr(float("inf")) == "inf" would poison the whole scrape.
    if not math.isfinite(number):

        return None

    return repr(number)


GAUGES = [
    ("battery_pdm_cell_soh_percent", "soh_pct", "SOH percent"),
    (
        "battery_pdm_cell_peak_temperature_celsius",
        "temp_max_c",
        "Peak temperature of the last discharge, Celsius",
    ),
    (
        "battery_pdm_cell_resistance_ohm",
        "resistance_ohm",
        "Internal resistance, ohm",
    ),
    (
        "battery_pdm_cell_discharge_cycle",
        "discharge_cycle",
        "Latest discharge index seen for the cell",
    ),
]


def read_cells(db_path):
    """
    Return the latest row per cell from live_pack_cells as a list of
    dicts, or an empty list when the database or table is not there
    yet (an empty scrape is not an error).
    """

    db_path = Path(db_path)

    if not db_path.exists():

        return []

    try:

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    except sqlite3.Error:

        return []

    try:

        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT cell_id, discharge_cycle, soh_pct, temp_max_c, "
            "resistance_ohm FROM live_pack_cells ORDER BY cell_id"
        ).fetchall()

    except sqlite3.Error:

        return []

    finally:

        conn.close()

    return [dict(row) for row in rows]


def render_metrics(cells):

    lines = []

    for name, column, help_text in GAUGES:

        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")

        for cell in cells:

            value = _number(cell.get(column))

            if value is None:

                continue

            label = _escape(cell.get("cell_id", ""))

            lines.append(f'{name}{{cell="{label}"}} {value}')

    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):

    db_path = DEFAULT_DB

    def do_GET(self):  # noqa: N802 (http.server's name)

        if self.path.split("?")[0] in ("/metrics", "/"):

            body = render_metrics(read_cells(self.db_path)).encode()

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/plain; version=0.0.4; charset=utf-8",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        else:

            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # keep the scrape logs quiet

        pass


def main(argv=None):

    argv = sys.argv[1:] if argv is None else argv

    host = os.environ.get("PDM_EXPORTER_HOST", DEFAULT_HOST)
    port = int(os.environ.get("PDM_EXPORTER_PORT", DEFAULT_PORT))
    db_path = Path(os.environ.get("PDM_LIVE_DB", DEFAULT_DB))

    Handler.db_path = db_path

    server = ThreadingHTTPServer((host, port), Handler)

    print(f"alert exporter on http://{host}:{port}/metrics (db={db_path})",
          flush=True)

    try:

        server.serve_forever()

    except KeyboardInterrupt:

        pass

    finally:

        server.server_close()


if __name__ == "__main__":

    main()
