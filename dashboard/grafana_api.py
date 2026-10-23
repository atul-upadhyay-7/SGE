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

from flask import Flask, jsonify, request
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
    # time-series style (cycle mapped to 2000-01-01 + n days)
    "soh_by_cycle",
    "capacity_by_cycle",
    "voltage_mean_by_cycle",
    "temperature_mean_by_cycle",
    "resistance_by_cycle",
    "discharge_duration_by_cycle",
    "soh_predicted_vs_actual",
    "rul_predicted_vs_actual",
    "rul_range_by_cycle",
    "anomaly_residual_by_cycle",
    "anomaly_fade_rate_by_cycle",
    "anomaly_flagged_events",

    # table and stat tile targets
    "table:stat_latest_soh",
    "table:stat_discharges_left",
    "table:stat_held_out_error",
    "table:stat_baseline_error",
    "table:rul_range",
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


BASE_MS = 946684800000  # 2000-01-01T00:00:00.000Z
DAY_MS = 86400000       # 1 day in milliseconds (24 * 3600 * 1000)


def cycle_to_ms(cycle):
    """
    Map discharge or cycle number n to 2000-01-01 + n days in epoch ms.
    Grafana reads this as a standard timestamp without 1970 pinning.
    """
    try:
        c = float(cycle)
        return int(BASE_MS + c * DAY_MS)
    except (ValueError, TypeError):
        return int(BASE_MS)


@app.route("/", methods=["GET"])
def health():
    return "OK"


@app.route("/search", methods=["POST"])
def search():
    body = request.get_json(silent=True) or {}
    target = body.get("target", "") or body.get("query", "")
    conn = get_db()
    try:
        if target == "groups":
            try:
                cols = [r[1] for r in conn.execute("PRAGMA table_info([nasa_ml_dataset])").fetchall()]
                if "group" in cols:
                    groups = [
                        r[0] for r in conn.execute(
                            "SELECT DISTINCT [group] FROM nasa_ml_dataset WHERE [group] IS NOT NULL ORDER BY [group]"
                        ).fetchall()
                    ]
                    return jsonify(groups)
            except Exception:
                pass
            return jsonify([])
        elif target == "cells" or target.startswith("cells"):
            if ":" in target:
                grp = target.split(":", 1)[1]
                # "cells:$group" arrives as "cells:All" when the group
                # selector is on All; that means no group filter.
                try:
                    cols = [r[1] for r in conn.execute("PRAGMA table_info([nasa_ml_dataset])").fetchall()]
                    if "group" in cols and grp not in ("", "All", "$group"):
                        cells = [
                            r[0] for r in conn.execute(
                                "SELECT DISTINCT cell_id FROM nasa_ml_dataset WHERE [group] = ? ORDER BY cell_id",
                                (grp,)
                            ).fetchall()
                        ]
                    else:
                        cells = [
                            r[0] for r in conn.execute(
                                "SELECT DISTINCT cell_id FROM nasa_ml_dataset WHERE cell_id IS NOT NULL ORDER BY cell_id"
                            ).fetchall()
                        ]
                except Exception:
                    cells = []
            else:
                cells = [
                    r[0] for r in conn.execute(
                        "SELECT DISTINCT cell_id FROM nasa_ml_dataset WHERE cell_id IS NOT NULL ORDER BY cell_id"
                    ).fetchall()
                ]
            return jsonify(cells)
    finally:
        conn.close()
    return jsonify(TARGETS + ["groups", "cells"])


@app.route("/query", methods=["POST"])
def query():
    body = request.get_json(silent=True) or {}
    results = []
    scoped_vars = body.get("scopedVars", {})

    for target_obj in body.get("targets", []):
        target = target_obj.get("target", "")

        if target.startswith("table:") or target.startswith("stat:"):
            results.append(handle_table(target, scoped_vars))
        else:
            results.extend(handle_timeseries(target, scoped_vars))

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


def get_cells_for_query(conn, table="nasa_ml_dataset", scoped_vars=None):
    """
    Retrieve cell list dynamically from the database based on dashboard variables.
    No hard-coded cell lists in the API.
    """
    cell_var = None
    group_var = None
    if scoped_vars:
        if "cell" in scoped_vars:
            cell_var = scoped_vars["cell"].get("value")
        if "group" in scoped_vars:
            group_var = scoped_vars["group"].get("value")

    if cell_var and cell_var != "All" and not str(cell_var).startswith("$"):
        return [cell_var]

    if not object_exists(conn, table):
        return []

    cols = [r[1] for r in conn.execute(f"PRAGMA table_info([{table}])").fetchall()]
    if group_var and group_var != "All" and not str(group_var).startswith("$") and "group" in cols:
        return [
            r[0] for r in conn.execute(
                f"SELECT DISTINCT cell_id FROM [{table}] WHERE [group] = ? ORDER BY cell_id",
                (group_var,)
            ).fetchall()
        ]

    return [
        r[0] for r in conn.execute(
            f"SELECT DISTINCT cell_id FROM [{table}] WHERE cell_id IS NOT NULL ORDER BY cell_id"
        ).fetchall()
    ]


def handle_timeseries(target, scoped_vars=None):
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
                         cycle_to_ms(r[0])]
                        for r in rows
                    ],
                })
        except sqlite3.Error:
            pass  # no live data yet: an empty panel, not an error

    elif target == "soh_by_cycle":
        cells = get_cells_for_query(conn, "nasa_ml_dataset", scoped_vars)
        for cell in cells:
            rows = conn.execute(
                "SELECT cycle, soh FROM nasa_ml_dataset WHERE cell_id = ? ORDER BY cycle",
                (cell,)
            ).fetchall()
            series.append({
                "target": f"SOH — {cell}",
                "datapoints": [[r["soh"], cycle_to_ms(r["cycle"])] for r in rows],
            })

    elif target == "capacity_by_cycle":
        cells = get_cells_for_query(conn, "nasa_ml_dataset", scoped_vars)
        for cell in cells:
            rows = conn.execute(
                "SELECT cycle, capacity_ah FROM nasa_ml_dataset WHERE cell_id = ? ORDER BY cycle",
                (cell,)
            ).fetchall()
            series.append({
                "target": f"Capacity — {cell}",
                "datapoints": [[r["capacity_ah"], cycle_to_ms(r["cycle"])] for r in rows],
            })

    elif target == "voltage_mean_by_cycle":
        cells = get_cells_for_query(conn, "nasa_ml_dataset", scoped_vars)
        for cell in cells:
            rows = conn.execute(
                "SELECT cycle, voltage_mean FROM nasa_ml_dataset WHERE cell_id = ? ORDER BY cycle",
                (cell,)
            ).fetchall()
            series.append({
                "target": f"Voltage Mean — {cell}",
                "datapoints": [[r["voltage_mean"], cycle_to_ms(r["cycle"])] for r in rows],
            })

    elif target == "temperature_mean_by_cycle":
        cells = get_cells_for_query(conn, "nasa_ml_dataset", scoped_vars)
        for cell in cells:
            rows = conn.execute(
                "SELECT cycle, temperature_mean FROM nasa_ml_dataset WHERE cell_id = ? ORDER BY cycle",
                (cell,)
            ).fetchall()
            series.append({
                "target": f"Temp Mean — {cell}",
                "datapoints": [[r["temperature_mean"], cycle_to_ms(r["cycle"])] for r in rows],
            })

    elif target == "resistance_by_cycle":
        cells = get_cells_for_query(conn, "nasa_ml_dataset", scoped_vars)
        for cell in cells:
            rows = conn.execute(
                "SELECT cycle, resistance_proxy_ohm FROM nasa_ml_dataset WHERE cell_id = ? ORDER BY cycle",
                (cell,)
            ).fetchall()
            series.append({
                "target": f"Resistance — {cell}",
                "datapoints": [[r["resistance_proxy_ohm"], cycle_to_ms(r["cycle"])] for r in rows],
            })

    elif target == "discharge_duration_by_cycle":
        cells = get_cells_for_query(conn, "nasa_ml_dataset", scoped_vars)
        for cell in cells:
            rows = conn.execute(
                "SELECT cycle, discharge_duration_s FROM nasa_ml_dataset WHERE cell_id = ? ORDER BY cycle",
                (cell,)
            ).fetchall()
            series.append({
                "target": f"Duration — {cell}",
                "datapoints": [[r["discharge_duration_s"], cycle_to_ms(r["cycle"])] for r in rows],
            })

    elif target == "soh_predicted_vs_actual":
        source_table = "soh_predictions_all" if object_exists(conn, "soh_predictions_all") else "nasa_ml_dataset"
        cells = get_cells_for_query(conn, source_table, scoped_vars)
        for cell in cells:
            rows = conn.execute(
                f"SELECT cycle, soh, predicted_soh FROM [{source_table}] WHERE cell_id = ? ORDER BY cycle",
                (cell,)
            ).fetchall()
            series.append({
                "target": f"Actual SOH — {cell}",
                "datapoints": [[r["soh"], cycle_to_ms(r["cycle"])] for r in rows],
            })
            series.append({
                "target": f"Predicted SOH — {cell}",
                "datapoints": [[r["predicted_soh"], cycle_to_ms(r["cycle"])] for r in rows],
            })

    elif target == "rul_predicted_vs_actual":
        source_table = "rul_predictions_all" if object_exists(conn, "rul_predictions_all") else "nasa_ml_dataset"
        cells = get_cells_for_query(conn, source_table, scoped_vars)
        for cell in cells:
            rows = conn.execute(
                f"SELECT cycle, rul_cycles_80, predicted_rul FROM [{source_table}] WHERE cell_id = ? ORDER BY cycle",
                (cell,)
            ).fetchall()
            series.append({
                "target": f"Actual RUL — {cell}",
                "datapoints": [[r["rul_cycles_80"], cycle_to_ms(r["cycle"])] for r in rows],
            })
            series.append({
                "target": f"Predicted RUL (indicative) — {cell}",
                "datapoints": [[r["predicted_rul"], cycle_to_ms(r["cycle"])] for r in rows],
            })

    elif target == "rul_range_by_cycle":
        # The shipped RUL is an indicative range, not a point: the
        # min-to-max of the trajectory matcher, the early-fade
        # extrapolation and the fleet-lifetime average at that
        # discharge (rul_prediction_bands.rul_low / rul_high). The
        # three methods are drawn inside the range so the reader sees
        # what sets its edges. The actual-RUL series is a separate,
        # observed quantity (cycles to 80% SOH) and comes from
        # rul_predictions_all.
        has_bands = object_exists(conn, "rul_prediction_bands")
        has_preds = object_exists(conn, "rul_predictions_all")
        has_extrap = object_exists(conn, "rul_extrapolation_bands")
        scope = "rul_prediction_bands" if has_bands else (
            "rul_predictions_all" if has_preds else "nasa_ml_dataset"
        )
        cells = get_cells_for_query(conn, scope, scoped_vars)
        for cell in cells:
            if has_preds:
                rows = conn.execute(
                    "SELECT cycle, rul_cycles_80 FROM rul_predictions_all "
                    "WHERE cell_id = ? AND rul_cycles_80 IS NOT NULL ORDER BY cycle",
                    (cell,)
                ).fetchall()
                if rows:
                    series.append({
                        "target": f"Actual RUL — {cell}",
                        "datapoints": [[r["rul_cycles_80"], cycle_to_ms(r["cycle"])] for r in rows],
                    })
            if has_bands:
                rows = conn.execute(
                    "SELECT cycle, predicted_rul, rul_low, rul_high, "
                    "extrapolated_rul, lifetime_average_rul "
                    "FROM rul_prediction_bands WHERE cell_id = ? ORDER BY cycle",
                    (cell,)
                ).fetchall()
                series.append({
                    "target": f"RUL Indicative Range Low — {cell}",
                    "datapoints": [[r["rul_low"], cycle_to_ms(r["cycle"])] for r in rows],
                })
                series.append({
                    "target": f"RUL Indicative Range High — {cell}",
                    "datapoints": [[r["rul_high"], cycle_to_ms(r["cycle"])] for r in rows],
                })
                series.append({
                    "target": f"Trajectory Matcher — {cell}",
                    "datapoints": [[r["predicted_rul"], cycle_to_ms(r["cycle"])] for r in rows],
                })
                series.append({
                    "target": f"Early-fade Extrapolation — {cell}",
                    "datapoints": [[r["extrapolated_rul"], cycle_to_ms(r["cycle"])] for r in rows],
                })
                series.append({
                    "target": f"Lifetime Average — {cell}",
                    "datapoints": [[r["lifetime_average_rul"], cycle_to_ms(r["cycle"])] for r in rows],
                })
            elif has_preds:
                rows = conn.execute(
                    "SELECT cycle, predicted_rul FROM rul_predictions_all "
                    "WHERE cell_id = ? ORDER BY cycle",
                    (cell,)
                ).fetchall()
                series.append({
                    "target": f"Predicted RUL (indicative) — {cell}",
                    "datapoints": [[r["predicted_rul"], cycle_to_ms(r["cycle"])] for r in rows],
                })

            # Fallback only when rul_prediction_bands is absent: the
            # second method on its own, so a checkout without the
            # range still shows both methods rather than one.
            if not has_bands and has_extrap:
                rows = conn.execute(
                    "SELECT cycle, extrapolated_rul "
                    "FROM rul_extrapolation_bands WHERE cell_id = ? ORDER BY cycle",
                    (cell,)
                ).fetchall()
                if rows:
                    series.append({
                        "target": f"Early-fade Extrapolation — {cell}",
                        "datapoints": [[r["extrapolated_rul"], cycle_to_ms(r["cycle"])] for r in rows],
                    })

    elif target == "anomaly_residual_by_cycle":
        if not object_exists(conn, "anomaly_cycle_report"):
            conn.close()
            return []
        cells = get_cells_for_query(conn, "anomaly_cycle_report", scoped_vars)
        for cell in cells:
            rows = conn.execute(
                "SELECT cycle, residual, residual_lower, residual_upper "
                "FROM anomaly_cycle_report WHERE cell_id = ? ORDER BY cycle",
                (cell,)
            ).fetchall()
            series.append({
                "target": f"Residual — {cell}",
                "datapoints": [
                    [r["residual"], cycle_to_ms(r["cycle"])] for r in rows
                ],
            })
            series.append({
                "target": f"Lower — {cell}",
                "datapoints": [
                    [r["residual_lower"], cycle_to_ms(r["cycle"])] for r in rows
                ],
            })
            series.append({
                "target": f"Upper — {cell}",
                "datapoints": [
                    [r["residual_upper"], cycle_to_ms(r["cycle"])] for r in rows
                ],
            })

    elif target == "anomaly_fade_rate_by_cycle":
        if not object_exists(conn, "anomaly_cycle_report"):
            conn.close()
            return []
        cells = get_cells_for_query(conn, "anomaly_cycle_report", scoped_vars)
        for cell in cells:
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
                    [r["fade_rate_pct_per_cycle"], cycle_to_ms(r["cycle"])] for r in rows
                ],
            })
            series.append({
                "target": f"Threshold — {cell}",
                "datapoints": [
                    [r["fade_rate_threshold"], cycle_to_ms(r["cycle"])] for r in rows
                ],
            })

    elif target == "anomaly_flagged_events":
        if not object_exists(conn, "anomaly_cycle_report"):
            conn.close()
            return []
        cells = get_cells_for_query(conn, "anomaly_cycle_report", scoped_vars)
        for cell in cells:
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
                        [r["residual"], cycle_to_ms(r["cycle"])] for r in rows
                    ],
                })

    conn.close()
    return series


# ── table handlers ─────────────────────────────────────────────

def scope_filter(conn, table, scoped_vars):
    """
    Restrict a per-cell table to the dashboard's selected cell or
    group. Returns a (sql_fragment, params) pair suitable for
    appending after an existing WHERE (or on its own). Empty when
    the selection is All, when there is no selection, or when the
    table has no cell_id column, so aggregate tables are untouched.
    """

    if not scoped_vars:

        return "", []

    try:

        cols = [
            r[1] for r in conn.execute(
                f"PRAGMA table_info([{table}])"
            ).fetchall()
        ]

    except sqlite3.Error:

        return "", []

    if "cell_id" not in cols:

        return "", []

    cell = (scoped_vars.get("cell") or {}).get("value")
    group = (scoped_vars.get("group") or {}).get("value")

    if cell and str(cell) not in ("", "All") and not str(cell).startswith("$"):

        return "cell_id = ?", [cell]

    if (
        group
        and str(group) not in ("", "All")
        and not str(group).startswith("$")
        and "group" in cols
    ):

        cells = get_cells_for_query(conn, table, scoped_vars)

        if not cells:

            return "1 = 0", []

        placeholders = ",".join("?" * len(cells))

        return f"cell_id IN ({placeholders})", list(cells)

    return "", []


def handle_table(target, scoped_vars=None):
    table_name = target.replace("table:", "").replace("stat:", "")
    conn = get_db()

    # ── stat tiles ─────────────────────────────────────────────
    # Every tile reads a value the pipeline measured into the
    # database. When the source table is absent the tile returns an
    # explicit error row rather than a plausible-looking constant:
    # a fabricated 82.5% SOH or "16 discharges left" is worse than a
    # blank tile, because it cannot be told apart from a real one.

    if table_name == "stat_latest_soh":
        row = conn.execute(
            "SELECT ROUND(soh, 2) FROM nasa_ml_dataset ORDER BY cycle DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row is None:
            return error_table(
                "No SOH rows in nasa_ml_dataset; run the pipeline."
            )
        return {
            "type": "table",
            "columns": [{"text": "Latest SOH (%)", "type": "number"}],
            "rows": [[row[0]]],
        }

    elif table_name == "stat_discharges_left":
        if not object_exists(conn, "rul_prediction_bands"):
            conn.close()
            return error_table(
                "No RUL prediction bands; run src/rul_trajectory.py."
            )
        # RUL ships as a range, not a point estimate: this is the
        # mean of each cell's latest indicative range, the
        # min-to-max of the trajectory matcher, the early-fade
        # extrapolation and the fleet-lifetime average at that
        # discharge (rul_prediction_bands.rul_low / rul_high).
        row = conn.execute(
            "SELECT AVG(rul_low), AVG(rul_high) "
            "FROM ("
            "  SELECT rul_low, rul_high "
            "  FROM rul_prediction_bands b "
            "  WHERE cycle = ("
            "    SELECT MAX(cycle) FROM rul_prediction_bands c "
            "    WHERE c.cell_id = b.cell_id"
            "  )"
            ")"
        ).fetchone()
        conn.close()
        if row is None or row[0] is None:
            return error_table("RUL prediction bands table is empty.")
        lo, hi = row[0], row[1]
        return {
            "type": "table",
            "columns": [
                {"text": "Range Low (discharges)", "type": "number"},
                {"text": "Range High (discharges)", "type": "number"},
            ],
            "rows": [[round(lo, 1), round(hi, 1)]],
        }

    elif table_name == "stat_held_out_error":
        row = None
        if object_exists(conn, "soh_nested_results"):
            row = conn.execute(
                "SELECT AVG(MAE) as v FROM soh_nested_results"
            ).fetchone()
        conn.close()
        if row is None or row["v"] is None:
            return error_table(
                "No held-out SOH results; run src/train_soh.py."
            )
        return {
            "type": "table",
            "columns": [{"text": "Held-Out SOH MAE (%)", "type": "number"}],
            "rows": [[round(row["v"], 2)]],
        }

    elif table_name == "stat_baseline_error":
        row = None
        if object_exists(conn, "soh_null_baselines"):
            row = conn.execute(
                "SELECT AVG(MAE) as v FROM soh_null_baselines "
                "WHERE baseline = 'constant_global_mean'"
            ).fetchone()
        conn.close()
        if row is None or row["v"] is None:
            return error_table(
                "No constant-mean baseline; run src/train_soh.py."
            )
        return {
            "type": "table",
            "columns": [{"text": "Baseline SOH MAE (%)", "type": "number"}],
            "rows": [[round(row["v"], 2)]],
        }

    elif table_name == "rul_range":
        has_bands = object_exists(conn, "rul_prediction_bands")
        has_preds = object_exists(conn, "rul_predictions_all")
        if not has_bands and not has_preds:
            conn.close()
            return error_table(
                "No RUL predictions; run src/train_rul.py and "
                "src/rul_trajectory.py."
            )

        # The shipped RUL is the indicative range in
        # rul_prediction_bands: rul_low/rul_high are the min and max of
        # the trajectory matcher, the early-fade extrapolation and the
        # fleet-lifetime average on that discharge, all stored by
        # src/rul_trajectory.py. The three method columns are shown
        # beside it so the range is read with what sets it. No single
        # method is presented as the answer.
        columns = [
            {"text": "Cell", "type": "string"},
            {"text": "Discharge", "type": "number"},
            {"text": "Actual Discharges", "type": "number"},
            {"text": "Indicative Range Low", "type": "number"},
            {"text": "Indicative Range High", "type": "number"},
            {"text": "Trajectory Matcher", "type": "number"},
            {"text": "Early-fade Extrapolation", "type": "number"},
            {"text": "Lifetime Average", "type": "number"},
        ]

        if has_bands:
            scope_sql, scope_params = scope_filter(
                conn, "rul_prediction_bands", scoped_vars
            )
            if has_preds:
                where = f" WHERE b.{scope_sql}" if scope_sql else ""
                sql = (
                    "SELECT b.cell_id, b.cycle, "
                    "ROUND(p.rul_cycles_80, 1) AS actual, "
                    "ROUND(b.rul_low, 1), ROUND(b.rul_high, 1), "
                    "ROUND(b.predicted_rul, 1), "
                    "ROUND(b.extrapolated_rul, 1), "
                    "ROUND(b.lifetime_average_rul, 1) "
                    "FROM rul_prediction_bands b "
                    "LEFT JOIN rul_predictions_all p "
                    "  ON p.cell_id = b.cell_id AND p.cycle = b.cycle"
                    + where
                    + " ORDER BY b.cell_id, b.cycle"
                )
            else:
                where = f" WHERE {scope_sql}" if scope_sql else ""
                sql = (
                    "SELECT cell_id, cycle, NULL AS actual, "
                    "ROUND(rul_low, 1), ROUND(rul_high, 1), "
                    "ROUND(predicted_rul, 1), "
                    "ROUND(extrapolated_rul, 1), "
                    "ROUND(lifetime_average_rul, 1) "
                    "FROM rul_prediction_bands" + where
                    + " ORDER BY cell_id, cycle"
                )
            rows = conn.execute(sql, scope_params).fetchall()
        else:
            # Pairs of predictions. Never the shipped output (the band
            # table is a pipeline stage), but it is what the panel can
            # read on a checkout that has only run train_rul.py.
            scope_sql, scope_params = scope_filter(
                conn, "rul_predictions_all", scoped_vars
            )
            where_sql = f" WHERE {scope_sql}" if scope_sql else ""
            rows = conn.execute(
                "SELECT cell_id, cycle, ROUND(rul_cycles_80, 1) AS actual, "
                "NULL, NULL, ROUND(predicted_rul, 1), NULL, NULL "
                "FROM rul_predictions_all" + where_sql +
                " ORDER BY cell_id, cycle",
                scope_params,
            ).fetchall()

        rows_data = [list(r) for r in rows]
        conn.close()
        return {"type": "table", "columns": columns, "rows": rows_data}

    elif table_name == "model_summary":
        # Every row is a database value. If a source table is missing
        # that row says so instead of falling back to a constant.
        columns = [
            {"text": "Metric", "type": "string"},
            {"text": "Value", "type": "string"},
        ]
        rows_data = []

        if object_exists(conn, "soh_nested_results"):
            row = conn.execute(
                "SELECT AVG(MAE) AS v, MIN(MAE) AS lo, MAX(MAE) AS hi, "
                "AVG(R2) AS r FROM soh_nested_results"
            ).fetchone()
            if row and row["v"] is not None:
                rows_data.append(["SOH Model", "XGBoost (nested LOCO)"])
                rows_data.append([
                    "SOH MAE (held-out)",
                    f"{row['v']:.2f}% (per-cell {row['lo']:.2f}–{row['hi']:.2f}%)",
                ])
                rows_data.append(["SOH R² (held-out)", f"{row['r']:.2f}"])
            else:
                rows_data.append(["SOH Model", "no held-out results"])
        else:
            rows_data.append(["SOH Model", "no held-out results"])

        if object_exists(conn, "soh_baselines"):
            row = conn.execute(
                "SELECT AVG(MAE) AS v FROM soh_baselines WHERE model = 'Coulomb'"
            ).fetchone()
            if row and row["v"] is not None:
                rows_data.append(
                    ["SOH Coulomb baseline", f"{row['v']:.2f}% (LOCO)"]
                )

        if object_exists(conn, "rul_trajectory_results"):
            row = conn.execute(
                "SELECT AVG(trajectory_mae) AS t, MIN(trajectory_mae) AS lo, "
                "MAX(trajectory_mae) AS hi, "
                "AVG(fleet_lifetime_baseline_mae) AS b "
                "FROM rul_trajectory_results"
            ).fetchone()
            if row and row["t"] is not None:
                rows_data.append(["RUL Method", "Reference-trajectory matching"])
                rows_data.append([
                    "RUL MAE",
                    f"{row['t']:.1f} discharges (per-cell {row['lo']:.1f}–"
                    f"{row['hi']:.1f}), lifetime baseline {row['b']:.1f}",
                ])
        else:
            rows_data.append(["RUL Method", "no trajectory results"])

        rows_data.append(["RUL Unit", "Discharges (discharge_index)"])
        rows_data.append([
            "Total Discharges",
            str(conn.execute("SELECT COUNT(*) FROM nasa_ml_dataset").fetchone()[0]),
        ])
        cells_list = ", ".join([
            r[0] for r in conn.execute(
                "SELECT DISTINCT cell_id FROM nasa_ml_dataset ORDER BY cell_id"
            ).fetchall()
        ])
        rows_data.append(["Database Cells", cells_list])
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
        scope_sql, scope_params = scope_filter(conn, table_name, scoped_vars)
        where = f" WHERE {scope_sql}" if scope_sql else ""
        cursor = conn.execute(
            f"SELECT * FROM [{table_name}]{where}", scope_params
        )
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
    host = os.environ.get("PDM_API_HOST", "127.0.0.1")
    print(f"Database: {DB_PATH}")
    print(f"Starting API server on http://{host}:8099")
    app.run(host=host, port=8099, debug=False)
