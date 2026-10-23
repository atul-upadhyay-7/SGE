"""Validate the live XGBoost SoH model on discharge-cycle data.

This is the production-path validation harness the project manager
asked for: it does NOT train or replace a model, and it does NOT use a
notebook shortcut. Every scored cycle goes through the exact live code
path — ``backend/app/ml/estimator.py::predict_soh``, which loads
``models/soh_window_all.ubj`` (cell-relative XGBoost).

Live model: XGBoost (``soh_window_all``) only.
ElasticNet is an offline benchmark only. It is never scored here as a
live model; when ``--offline-heldout`` is supplied its held-out numbers
are copied into the report under ``offline_benchmark`` and labelled as
such.

Input CSV format (one row per discharge cycle, in discharge order)
------------------------------------------------------------------
Required columns:

  cell_id            identifier of the cell / pack under test
  discharge_index    integer order of the discharge for that cell
  vw_time_s          seconds inside the trained 4.0-3.4 V window
  vw_capacity_ah     ampere-hours delivered inside the window
  vw_voltage_mean    mean cell voltage inside the window (volts)
  vw_dqdv_peak_v     voltage of the dQ/dV peak inside the window
  vw_dqdv_peak_height  height of the dQ/dV peak (Ah/V)
  current_mean       mean discharge current (amperes)
  actual_soh_pct     measured SoH (percent) for that full discharge,
                     from Coulomb counting — the ground truth the
                     prediction is scored against

The first ``WINDOW_REFERENCE_WINDOW`` (5) discharges of each cell form
that cell's reference baseline and are reported as ``reference`` rows,
not scored. A cell with fewer than
``WINDOW_MIN_REFERENCE_DISCHARGES`` (3) discharges cannot form a
baseline: its rows are refused with ``insufficient_reference``.
Later discharges are scored; the estimator may still refuse a row
(``out_of_trained_range`` / ``window_range``) and that refusal is
reported, never hidden or replaced with a guessed number.

Usage
-----
  # Score a real-sensor export:
  python scripts/validate_real_data.py --input real_sensor_cycles.csv \
      --source real_sensor

  # Build a lab-derived SAMPLE input from the cleaned NASA dataset
  # (this is lab data, NOT real sensor data):
  python scripts/validate_real_data.py --generate-sample \
      data/processed/validation/sample_input.csv

  # Score that sample (honest source label):
  python scripts/validate_real_data.py \
      --input data/processed/validation/sample_input.csv \
      --source lab_sample_nasa \
      --offline-heldout data/processed/soh_window_heldout_B0005.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "backend"))

# Feature contract comes from src/features.py — the same module the
# live estimator imports — so this harness cannot drift from it.
from features import (  # noqa: E402
    OUT_OF_TRAINED_RANGE,
    WINDOW_FEATURES,
    WINDOW_MIN_REFERENCE_DISCHARGES,
    WINDOW_NORMALIZATION_INPUTS,
    WINDOW_REFERENCE_WINDOW,
)

REQUIRED_COLUMNS = [
    "cell_id",
    "discharge_index",
    *WINDOW_FEATURES,
    *WINDOW_NORMALIZATION_INPUTS,
    "actual_soh_pct",
]

MODEL_NAME = "soh_window_all"
MODEL_ARTIFACT = "models/soh_window_all.ubj"
MODEL_ALGORITHM = "XGBoost"

DEFAULT_OUTPUT_JSON = ROOT / "data" / "processed" / "validation" / "latest_validation.json"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "processed" / "validation"


def compute_metrics(rows: list[dict]) -> dict:
    """Summary metrics over scored rows only (refusals are excluded)."""
    scored = [r for r in rows if r["status"] == "scored"]
    refused = [r for r in rows if r["status"] == "refused"]
    errors = [float(r["error_pct"]) for r in scored]
    n = len(errors)
    return {
        "total_rows": len(rows),
        "reference_rows": sum(1 for r in rows if r["status"] == "reference"),
        "scored_rows": n,
        "refused_rows": len(refused),
        "refusals_by_reason": _count_by(refused, "refusal_reason"),
        "mae_pct": round(sum(abs(e) for e in errors) / n, 4) if n else None,
        "rmse_pct": round(math.sqrt(sum(e * e for e in errors) / n), 4) if n else None,
        "bias_pct": round(sum(errors) / n, 4) if n else None,
        "max_abs_error_pct": round(max((abs(e) for e in errors), default=0.0), 4) if n else None,
    }


def _count_by(rows: list[dict], key: str) -> dict:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(key) or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return counts


def validate_records(records: list[dict], predict_fn=None) -> list[dict]:
    """Score records cell-by-cell through ``predict_fn``.

    ``predict_fn`` defaults to the live estimator; tests inject a fake
    with the same contract (float = score, OUT_OF_TRAINED_RANGE string
    = refusal, InsufficientReferenceError / WindowRangeError raised).
    """
    if predict_fn is None:
        from app.ml.estimator import predict_soh as predict_fn  # noqa: PLC0415

    from app.ml.estimator import InsufficientReferenceError  # noqa: PLC0415

    from features import WindowRangeError  # noqa: PLC0415

    by_cell: dict[str, list[dict]] = {}
    for record in records:
        by_cell.setdefault(str(record["cell_id"]), []).append(record)

    out: list[dict] = []
    for cell_id, cell_rows in by_cell.items():
        ordered = sorted(cell_rows, key=lambda r: int(r["discharge_index"]))
        early = ordered[:WINDOW_REFERENCE_WINDOW]
        if len(ordered) < WINDOW_MIN_REFERENCE_DISCHARGES:
            for row in ordered:
                out.append(_result(row, "refused", refusal_reason="insufficient_reference"))
            continue
        for row in early:
            out.append(_result(row, "reference"))
        for row in ordered[WINDOW_REFERENCE_WINDOW:]:
            window = {name: float(row[name]) for name in WINDOW_FEATURES + WINDOW_NORMALIZATION_INPUTS}
            early_rows = [
                {name: float(r[name]) for name in WINDOW_FEATURES + WINDOW_NORMALIZATION_INPUTS}
                for r in early
            ]
            try:
                prediction = predict_fn(window, early_rows)
            except InsufficientReferenceError:
                out.append(_result(row, "refused", refusal_reason="insufficient_reference"))
                continue
            except WindowRangeError:
                out.append(_result(row, "refused", refusal_reason="window_range"))
                continue
            if isinstance(prediction, str):
                reason = (
                    "out_of_trained_range"
                    if prediction == OUT_OF_TRAINED_RANGE
                    else "refused"
                )
                out.append(_result(row, "refused", refusal_reason=reason))
                continue
            out.append(_result(row, "scored", predicted=float(prediction)))
    out.sort(key=lambda r: (r["cell_id"], r["discharge_index"]))
    return out


def _result(row: dict, status: str, predicted: float | None = None,
            refusal_reason: str | None = None) -> dict:
    actual = float(row["actual_soh_pct"])
    result = {
        "cell_id": str(row["cell_id"]),
        "discharge_index": int(row["discharge_index"]),
        "actual_soh_pct": round(actual, 4),
        "predicted_soh_pct": round(predicted, 4) if predicted is not None else None,
        "error_pct": round(predicted - actual, 4) if predicted is not None else None,
        "abs_error_pct": round(abs(predicted - actual), 4) if predicted is not None else None,
        "status": status,
        "refusal_reason": refusal_reason,
    }
    return result


def offline_benchmark(heldout_path: Path) -> dict | None:
    """ElasticNet/XGBoost numbers from a held-out CSV — OFFLINE only.

    The held-out file (``soh_window_heldout_B0005.csv``) carries both
    ``predicted_xgboost`` and ``predicted_elasticnet`` for a cell the
    window model never trained on in that protocol. These numbers are
    a benchmark comparison; ElasticNet is not deployed and is not a
    live improvement.
    """
    if not heldout_path.is_file():
        return None
    with heldout_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or "predicted_elasticnet" not in rows[0]:
        return None

    def mae(column: str) -> float:
        return round(
            sum(abs(float(r[column]) - float(r["soh"])) for r in rows) / len(rows), 4
        )

    return {
        "label": "Offline benchmark only — not live, not deployed",
        "source_file": heldout_path.name,
        "cell_id": rows[0].get("cell_id"),
        "rows": len(rows),
        "xgboost_mae_pct": mae("predicted_xgboost"),
        "elasticnet_mae_pct": mae("predicted_elasticnet"),
    }


def build_report(records: list[dict], *, source: str, dataset_name: str,
                 predict_fn=None, offline: dict | None = None) -> dict:
    rows = validate_records(records, predict_fn=predict_fn)
    notes = [
        "Live prediction is XGBoost (soh_window_all) only.",
        "ElasticNet is an offline benchmark only — not deployed, "
        "not a live improvement.",
        "SGE is a tested PoC, not a hardware-proven BMS.",
    ]
    if source != "real_sensor":
        notes.append(
            "This run is NOT real-sensor validation: its source is "
            f"'{source}'. Lab-sample cells come from the NASA dataset "
            "the model trained on, so these numbers verify the harness "
            "and pipeline, not held-out accuracy. Real-sensor "
            "validation is pending."
        )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": source,
        "dataset": dataset_name,
        "model": {
            "name": MODEL_NAME,
            "artifact": MODEL_ARTIFACT,
            "algorithm": MODEL_ALGORITHM,
            "live": True,
        },
        "summary": compute_metrics(rows),
        "offline_benchmark": offline,
        "notes": notes,
        "rows": rows,
    }


def generate_sample(output: Path, cells: list[str] | None = None) -> Path:
    """Build a sample input from the cleaned NASA lab dataset.

    The clean dataset already carries every raw window column, so no
    column is fabricated: ``soh`` (percent, from full-discharge
    Coulomb counting) becomes ``actual_soh_pct``. This is LAB data —
    a format/workflow sample, not real sensor data.
    """
    import pandas as pd  # noqa: PLC0415

    clean = ROOT / "data" / "processed" / "nasa_ml_dataset_clean.csv"
    df = pd.read_csv(clean)
    wanted = cells or ["B0005", "B0029", "B0053"]
    sample = df[df["cell_id"].isin(wanted)].copy()
    sample = sample.sort_values(["cell_id", "discharge_index"])
    out = sample[
        ["cell_id", "discharge_index", *WINDOW_FEATURES,
         *WINDOW_NORMALIZATION_INPUTS, "soh"]
    ].rename(columns={"soh": "actual_soh_pct"})
    output.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output, index=False)
    return output


def read_records(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"input is missing required columns: {', '.join(missing)}")
        return list(reader)


def write_csv_report(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["cell_id", "discharge_index", "actual_soh_pct", "predicted_soh_pct",
              "error_pct", "abs_error_pct", "status", "refusal_reason"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fields})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, help="input CSV (see module docstring)")
    parser.add_argument("--source", default="real_sensor",
                        help="provenance label stored in the report (default: real_sensor)")
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-csv", type=Path,
                        help="per-cycle CSV report (default: alongside the JSON)")
    parser.add_argument("--offline-heldout", type=Path,
                        help="held-out CSV with predicted_elasticnet (offline benchmark only)")
    parser.add_argument("--generate-sample", type=Path, metavar="OUTPUT_CSV",
                        help="write a lab-derived sample input CSV and exit")
    parser.add_argument("--sample-cells", nargs="*",
                        help="cell_ids for --generate-sample (default B0005 B0029 B0053)")
    args = parser.parse_args(argv)

    if args.generate_sample:
        path = generate_sample(args.generate_sample, args.sample_cells)
        print(f"wrote lab-derived sample input (NOT real sensor data): {path}")
        return 0
    if not args.input:
        parser.error("--input is required (or use --generate-sample)")

    records = read_records(args.input)
    offline = offline_benchmark(args.offline_heldout) if args.offline_heldout else None
    report = build_report(records, source=args.source,
                          dataset_name=args.input.name, offline=offline)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    csv_path = args.output_csv or args.output_json.with_suffix(".csv")
    write_csv_report(report["rows"], csv_path)
    s = report["summary"]
    print(f"validation report: {args.output_json}")
    print(f"per-cycle report:  {csv_path}")
    print(f"source={report['source']} dataset={report['dataset']} model={MODEL_NAME} (XGBoost, live)")
    print(f"rows={s['total_rows']} reference={s['reference_rows']} "
          f"scored={s['scored_rows']} refused={s['refused_rows']} {s['refusals_by_reason']}")
    print(f"MAE={s['mae_pct']}% RMSE={s['rmse_pct']}% bias={s['bias_pct']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
