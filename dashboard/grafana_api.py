"""
grafana_api.py
--------------
Lightweight JSON API server for Grafana SimpleJSON / Infinity datasource.
Serves battery PDM data from the SQLite database.

Endpoints:
  GET  /                  → health check
  POST /search            → list available metrics/tables
  POST /query             → return time-series or table data
  POST /annotations       → annotations (stub)
"""

import os
import sqlite3
from flask import Flask, request, jsonify
from flask_cors import CORS

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "data", "battery_pdm.db")

# Live pack tables are written by src/pack/bridge.py into their
# own file, because load_to_sqlite.py rebuilds DB_PATH from
# scratch and would wipe them. The file is attached as "live"
# when it exists, so its tables resolve by bare name and the
# dashboard reads them like any other table.
LIVE_DB_PATH = os.environ.get(
    "PDM_LIVE_DB",
    os.path.join(BASE, "data", "live_pack.db"),
)

app = Flask(__name__)
CORS(app)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if os.path.exists(LIVE_DB_PATH):
        conn.execute("ATTACH DATABASE ? AS live", (LIVE_DB_PATH,))

    return conn


def object_exists(conn, name):
    """
    Whether a table or view of this name exists in the database.

    Table names reach here straight from a dashboard panel, so
    the generic branch cannot assume the name is real. The check
    is a bound parameter rather than string formatting, which
    keeps it correct for any name at all.
    """

    row = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type IN ('table', 'view') AND name = ?",
        (name,),
    ).fetchone()

    if row is None and os.path.exists(LIVE_DB_PATH):
        row = conn.execute(
            "SELECT name FROM live.sqlite_master "
            "WHERE type IN ('table', 'view') AND name = ?",
            (name,),
        ).fetchone()

    return row is not None


# ── available targets ──────────────────────────────────────────
TARGETS = [
    # time-series style (cycle as x-axis)
    "soh_by_cycle",
    "capacity_by_cycle",
    "voltage_mean_by_cycle",
    "temperature_mean_by_cycle",
    "resistance_by_cycle",
    "discharge_duration_by_cycle",
    "soh_predicted_vs_actual",
    "rul_predicted_vs_actual",
    "anomaly_residual_by_cycle",
    "anomaly_fade_rate_by_cycle",
    "anomaly_flagged_events",

    # table targets
    "table:soh_results",
    "table:soh_nested_results",
    "table:soh_baselines",
    "table:soh_feature_importance",
    "table:soh_bias_report",
    "table:rul_results",
    "table:rul_degradation_rates",
    "table:rul_null_baselines",
    "table:nasa_ml_dataset",
    "table:model_summary",
    "table:anomaly_summary",
    "table:anomaly_events",
    "table:anomaly_cycle_report",

    # live pack monitoring (written by src/pack/bridge.py)
    "live_soh_by_cycle",
    "live_voltage_end_by_cycle",
    "live_resistance_by_cycle",
    "live_temperature_by_cycle",
    "live_voltage_spread_by_cycle",
    "table:live_pack_cells",
    "table:live_pack_alerts",
    "table:live_pack_status",
]


@app.route("/", methods=["GET"])
def health():
    return "OK"


@app.route("/search", methods=["POST"])
def search():
    return jsonify(TARGETS)


@app.route("/query", methods=["POST"])
def query():
    body = request.get_json()
    results = []

    for target_obj in body.get("targets", []):
        target = target_obj.get("target", "")

        if target.startswith("table:"):
            results.append(handle_table(target))
        else:
            results.extend(handle_timeseries(target))

    return jsonify(results)


@app.route("/annotations", methods=["POST"])
def annotations():
    return jsonify([])


# ── time-series handlers ──────────────────────────────────────

# live_pack_history column, legend label, unit scale
LIVE_SERIES = {
    "live_soh_by_cycle": ("soh_pct", "SOH %", 1.0),
    "live_voltage_end_by_cycle": ("v_end_v", "End voltage V", 1.0),
    "live_resistance_by_cycle": (
        "resistance_ohm", "Resistance ohm", 1.0
    ),
    "live_temperature_by_cycle": ("temp_max_c", "Peak temp C", 1.0),
    "live_voltage_spread_by_cycle": (
        "voltage_spread_mv", "Spread mV", 1.0
    ),
}


def handle_timeseries(target):
    conn = get_db()
    series = []

    if target in LIVE_SERIES:
        column, label, scale = LIVE_SERIES[target]
        try:
            cells = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT cell_id FROM live_pack_history "
                    "WHERE cell_id IS NOT NULL ORDER BY cell_id"
                )
            ]
            for cell in cells:
                rows = conn.execute(
                    f"SELECT discharge_cycle, {column} FROM "
                    "live_pack_history WHERE cell_id = ? "
                    "ORDER BY discharge_cycle",
                    (cell,),
                ).fetchall()
                series.append({
                    "target": f"{label} - {cell}",
                    "datapoints": [
                        [r[1] * scale if r[1] is not None else None,
                         r[0]]
                        for r in rows
                    ],
                })
        except sqlite3.Error:
            pass  # no live data yet: an empty panel, not an error

    elif target == "soh_by_cycle":
        for cell in ["B0005", "B0006", "B0007", "B0018"]:
            rows = conn.execute(
                "SELECT cycle, soh FROM nasa_ml_dataset WHERE cell_id = ? ORDER BY cycle",
                (cell,)
            ).fetchall()
            series.append({
                "target": f"SOH — {cell}",
                "datapoints": [[r["soh"], r["cycle"]] for r in rows],
            })

    elif target == "capacity_by_cycle":
        for cell in ["B0005", "B0006", "B0007", "B0018"]:
            rows = conn.execute(
                "SELECT cycle, capacity_ah FROM nasa_ml_dataset WHERE cell_id = ? ORDER BY cycle",
                (cell,)
            ).fetchall()
            series.append({
                "target": f"Capacity — {cell}",
                "datapoints": [[r["capacity_ah"], r["cycle"]] for r in rows],
            })

    elif target == "voltage_mean_by_cycle":
        for cell in ["B0005", "B0006", "B0007", "B0018"]:
            rows = conn.execute(
                "SELECT cycle, voltage_mean FROM nasa_ml_dataset WHERE cell_id = ? ORDER BY cycle",
                (cell,)
            ).fetchall()
            series.append({
                "target": f"Voltage Mean — {cell}",
                "datapoints": [[r["voltage_mean"], r["cycle"]] for r in rows],
            })

    elif target == "temperature_mean_by_cycle":
        for cell in ["B0005", "B0006", "B0007", "B0018"]:
            rows = conn.execute(
                "SELECT cycle, temperature_mean FROM nasa_ml_dataset WHERE cell_id = ? ORDER BY cycle",
                (cell,)
            ).fetchall()
            series.append({
                "target": f"Temp Mean — {cell}",
                "datapoints": [[r["temperature_mean"], r["cycle"]] for r in rows],
            })

    elif target == "resistance_by_cycle":
        for cell in ["B0005", "B0006", "B0007", "B0018"]:
            rows = conn.execute(
                "SELECT cycle, resistance_proxy_ohm FROM nasa_ml_dataset WHERE cell_id = ? ORDER BY cycle",
                (cell,)
            ).fetchall()
            series.append({
                "target": f"Resistance — {cell}",
                "datapoints": [[r["resistance_proxy_ohm"], r["cycle"]] for r in rows],
            })

    elif target == "discharge_duration_by_cycle":
        for cell in ["B0005", "B0006", "B0007", "B0018"]:
            rows = conn.execute(
                "SELECT cycle, discharge_duration_s FROM nasa_ml_dataset WHERE cell_id = ? ORDER BY cycle",
                (cell,)
            ).fetchall()
            series.append({
                "target": f"Duration — {cell}",
                "datapoints": [[r["discharge_duration_s"], r["cycle"]] for r in rows],
            })

    elif target == "soh_predicted_vs_actual":
        for cell in ["B0005", "B0006", "B0007", "B0018"]:
            rows = conn.execute(
                "SELECT cycle, soh, predicted_soh FROM soh_predictions_all WHERE cell_id = ? ORDER BY cycle",
                (cell,)
            ).fetchall()
            series.append({
                "target": f"Actual SOH — {cell}",
                "datapoints": [[r["soh"], r["cycle"]] for r in rows],
            })
            series.append({
                "target": f"Predicted SOH — {cell}",
                "datapoints": [[r["predicted_soh"], r["cycle"]] for r in rows],
            })

    elif target == "rul_predicted_vs_actual":
        for cell in ["B0005", "B0006", "B0007", "B0018"]:
            rows = conn.execute(
                "SELECT cycle, rul_cycles_80, predicted_rul FROM rul_predictions_all WHERE cell_id = ? ORDER BY cycle",
                (cell,)
            ).fetchall()
            series.append({
                "target": f"Actual RUL — {cell}",
                "datapoints": [[r["rul_cycles_80"], r["cycle"]] for r in rows],
            })
            series.append({
                "target": f"Predicted RUL — {cell}",
                "datapoints": [[r["predicted_rul"], r["cycle"]] for r in rows],
            })

    elif target == "anomaly_residual_by_cycle":
        if not object_exists(conn, "anomaly_cycle_report"):
            conn.close()
            return []
        for cell in ["B0005", "B0006", "B0007", "B0018"]:
            rows = conn.execute(
                "SELECT cycle, residual, residual_lower, residual_upper "
                "FROM anomaly_cycle_report WHERE cell_id = ? ORDER BY cycle",
                (cell,)
            ).fetchall()
            series.append({
                "target": f"Residual — {cell}",
                "datapoints": [
                    [r["residual"], r["cycle"]] for r in rows
                ],
            })
            series.append({
                "target": f"Lower — {cell}",
                "datapoints": [
                    [r["residual_lower"], r["cycle"]] for r in rows
                ],
            })
            series.append({
                "target": f"Upper — {cell}",
                "datapoints": [
                    [r["residual_upper"], r["cycle"]] for r in rows
                ],
            })

    elif target == "anomaly_fade_rate_by_cycle":
        if not object_exists(conn, "anomaly_cycle_report"):
            conn.close()
            return []
        for cell in ["B0005", "B0006", "B0007", "B0018"]:
            rows = conn.execute(
                "SELECT cycle, fade_rate_pct_per_cycle, fade_rate_threshold "
                "FROM anomaly_cycle_report "
                "WHERE cell_id = ? AND fade_rate_pct_per_cycle IS NOT NULL "
                "ORDER BY cycle",
                (cell,)
            ).fetchall()
            series.append({
                "target": f"Fade Rate — {cell}",
                "datapoints": [
                    [r["fade_rate_pct_per_cycle"], r["cycle"]] for r in rows
                ],
            })
            series.append({
                "target": f"Threshold — {cell}",
                "datapoints": [
                    [r["fade_rate_threshold"], r["cycle"]] for r in rows
                ],
            })

    elif target == "anomaly_flagged_events":
        if not object_exists(conn, "anomaly_cycle_report"):
            conn.close()
            return []
        for cell in ["B0005", "B0006", "B0007", "B0018"]:
            rows = conn.execute(
                "SELECT cycle, residual "
                "FROM anomaly_cycle_report "
                "WHERE cell_id = ? AND anomaly = 1 ORDER BY cycle",
                (cell,)
            ).fetchall()
            if rows:
                series.append({
                    "target": f"Flagged — {cell}",
                    "datapoints": [
                        [r["residual"], r["cycle"]] for r in rows
                    ],
                })

    conn.close()
    return series


# ── table handlers ─────────────────────────────────────────────

def handle_table(target):
    table_name = target.replace("table:", "")
    conn = get_db()

    if table_name == "model_summary":
        # build a summary table
        soh_mae = conn.execute("SELECT AVG(MAE) as v FROM soh_nested_results").fetchone()["v"]
        soh_r2 = conn.execute("SELECT AVG(R2) as v FROM soh_nested_results").fetchone()["v"]
        rul_best = conn.execute(
            "SELECT model, AVG(MAE) as mae FROM rul_results GROUP BY model ORDER BY mae LIMIT 1"
        ).fetchone()

        columns = [
            {"text": "Metric", "type": "string"},
            {"text": "Value", "type": "string"},
        ]
        rows_data = [
            ["SOH Model", "XGBoost (nested CV)"],
            ["SOH MAE", f"{soh_mae:.4f}%"],
            ["SOH R²", f"{soh_r2:.4f}"],
            ["RUL Best Model", rul_best["model"] if rul_best else "N/A"],
            ["RUL MAE", f"{rul_best['mae']:.1f} cycles" if rul_best else "N/A"],
            ["Total Cycles", str(conn.execute("SELECT COUNT(*) FROM nasa_ml_dataset").fetchone()[0])],
            ["Cells", "B0005, B0006, B0007, B0018"],
        ]
        conn.close()
        return {"type": "table", "columns": columns, "rows": rows_data}

    # generic table
    if not object_exists(conn, table_name):
        conn.close()
        return error_table(
            f"Unknown table: {table_name}. "
            "POST /search lists the available targets."
        )

    try:
        cursor = conn.execute(f"SELECT * FROM [{table_name}]")
        col_names = [d[0] for d in cursor.description]
        rows_data = [list(r) for r in cursor.fetchall()]

        # Declared types come from PRAGMA table_info, not from
        # cursor.description: on this interpreter the latter
        # reports a null type for every column, which is why
        # every column used to reach Grafana as a string.
        declared = [
            row[2]
            for row in conn.execute(
                f"PRAGMA table_info([{table_name}])"
            ).fetchall()
        ]
    except sqlite3.Error as error:
        return error_table(
            f"Could not read {table_name}: {error}"
        )
    finally:
        conn.close()

    if len(declared) != len(col_names):

        # PRAGMA and SELECT disagree on the column set, which
        # would silently misalign names against types. Fall
        # back to all-string rather than render a table with
        # the wrong types on it.
        declared = [None] * len(col_names)

    columns = [
        {
            "text": name,
            "type": sqlite_type_to_frame_type(column_type),
        }
        for name, column_type in zip(col_names, declared)
    ]

    return {"type": "table", "columns": columns, "rows": rows_data}


# SQLite declared types that carry numbers. The simpod JSON
# frame format distinguishes "number" from "string", and Grafana
# uses that to right-align and format numeric columns.
NUMERIC_SQLITE_TYPES = frozenset(
    {
        "INTEGER",
        "INT",
        "BIGINT",
        "SMALLINT",
        "TINYINT",
        "REAL",
        "DOUBLE",
        "DOUBLE PRECISION",
        "FLOAT",
        "NUMERIC",
        "DECIMAL",
        "BOOLEAN",
    }
)


def sqlite_type_to_frame_type(declared):
    """
    Map a SQLite declared column type to a simpod JSON frame
    type.

    Every column used to be declared "string", which made
    Grafana render MAE and RMSE left-aligned as text with no
    numeric formatting. Returning "number" for numeric
    columns lets the table panel format them properly.

    The declared type can be None or empty for expressions and
    some views, so anything unrecognised falls back to
    "string" rather than being guessed at.
    """

    if not declared:

        return "string"

    normalised = str(declared).strip().upper()

    if normalised in NUMERIC_SQLITE_TYPES:

        return "number"

    # SQLite is loosely typed and a numeric column may be
    # declared with a width or a suffix, e.g. "REAL(10,4)" or
    # "DOUBLE PRECISION NOT NULL".
    head = normalised.split("(")[0].split()[0] if normalised else ""

    if head in NUMERIC_SQLITE_TYPES:

        return "number"

    return "string"


def error_table(message):
    """
    A one-row table carrying a failure, in the panel's own shape.

    An unknown or unreadable table is reported inside the
    response rather than as an HTTP error. /query answers every
    panel in one call, so raising here would blank every other
    panel on the dashboard over a single mistyped target, and
    the panel at fault would show nothing about why.
    """

    return {
        "type": "table",
        "columns": [{"text": "error", "type": "string"}],
        "rows": [[message]],
    }


if __name__ == "__main__":
    print(f"Database: {DB_PATH}")
    print(f"Starting API server on http://localhost:8099")
    app.run(host="0.0.0.0", port=8099, debug=False)
