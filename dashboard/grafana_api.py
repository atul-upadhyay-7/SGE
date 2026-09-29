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
import json
import sqlite3
from flask import Flask, request, jsonify
from flask_cors import CORS

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "data", "battery_pdm.db")

app = Flask(__name__)
CORS(app)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
        target_type = target_obj.get("type", "timeserie")

        if target.startswith("table:"):
            results.append(handle_table(target))
        else:
            results.extend(handle_timeseries(target))

    return jsonify(results)


@app.route("/annotations", methods=["POST"])
def annotations():
    return jsonify([])


# ── time-series handlers ──────────────────────────────────────

def handle_timeseries(target):
    conn = get_db()
    series = []

    if target == "soh_by_cycle":
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
    cursor = conn.execute(f"SELECT * FROM [{table_name}]")
    col_names = [d[0] for d in cursor.description]
    rows_data = [list(r) for r in cursor.fetchall()]
    conn.close()

    columns = [{"text": c, "type": "string"} for c in col_names]
    return {"type": "table", "columns": columns, "rows": rows_data}


if __name__ == "__main__":
    print(f"Database: {DB_PATH}")
    print(f"Starting API server on http://localhost:8099")
    app.run(host="0.0.0.0", port=8099, debug=False)
