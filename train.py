#!/usr/bin/env python3
"""
train.py
--------
One command to retrain every model in this repo, check the result
and write a report.

    python train.py --quick      # about 1-2 min on a laptop, smoke test
    python train.py              # full run, honest nested search
    python train.py --check      # preflight only, trains nothing

What it does, in order
    0. Preflight: Python and package versions, raw data present.
    1. Backs up models/ and data/processed/ to artifacts/prev_<time>/
       so a bad run can be rolled back by copying the folders back.
    2. Runs the stages below, each logged to logs/train_<time>/.
    3. Gates: reads the result files and fails loudly if a metric is
       outside the range this project has verified, or an artifact is
       missing or does not load.
    4. Writes reports/train_report_<time>.md and .json.

Stages (same order as run_pipeline.py, plus the two that make the
pack monitor and the RUL reference curves current)
    load_data, preprocessing, data_quality, train_soh, train_rul,
    rul_trajectory, anomaly, pack_evaluate

Exit code 0 only when every stage ran and every gate passed.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROC = ROOT / "data" / "processed"
MODELS = ROOT / "models"

STAGES = [
    ("load_data", "src/load_data.py"),
    ("preprocessing", "src/preprocessing.py"),
    ("data_quality", "src/data_quality.py"),
    ("train_soh", "src/train_soh.py"),
    ("train_rul", "src/train_rul.py"),
    ("rul_trajectory", "src/rul_trajectory.py"),
    ("anomaly", "src/anomaly.py"),
    ("pack_evaluate", "src/pack/evaluate.py"),
]
# Expected artifacts after a successful run.
ARTIFACTS = [
    "models/soh_xgb_all.joblib",
    "models/soh_xgb_B0005.joblib",
    "models/soh_xgb_B0006.joblib",
    "models/soh_xgb_B0007.joblib",
    "models/soh_xgb_B0018.joblib",
    "models/rul_xgb_all.joblib",
    "models/rul_reference_curves.json",
    "models/pack_iforest.joblib",
    "data/processed/nasa_ml_dataset.csv",
    "data/processed/soh_nested_results.csv",
    "data/processed/rul_trajectory_results.csv",
    "data/processed/pack_detection_summary.csv",
]

# Gate limits. Verified values on the four NASA cells with the full
# search: SOH nested MAE 3.47, worst cell 7.09, trajectory RUL 21.0
# against a 32.7 baseline. The quick search is allowed more slack.
LIMITS = {
    "full": {"soh_mae": 5.0, "soh_worst_cell": 10.0},
    "quick": {"soh_mae": 6.0, "soh_worst_cell": 12.0},
}

REQUIRED_PACKAGES = [
    "numpy", "pandas", "scipy", "sklearn", "xgboost", "joblib",
    "flask", "flask_cors", "pytest", "paho.mqtt.client",
]


def preflight():
    """Return (ok, lines)."""

    ok = True
    lines = []

    if sys.version_info < (3, 12):
        ok = False
        lines.append(
            f"FAIL python {platform.python_version()} (need 3.12+ for pinned requirements)"
        )
    else:
        lines.append(f"ok   python {platform.python_version()}")

    for name in REQUIRED_PACKAGES:
        try:
            mod = __import__(name)
            lines.append(
                f"ok   {name} {getattr(mod, '__version__', '?')}"
            )
        except ImportError:
            ok = False
            lines.append(
                f"FAIL {name} missing: pip install -r requirements.txt"
            )

    sys.path.insert(0, str(ROOT / "src"))
    try:
        from load_data import resolve_raw_dir

        os.chdir(ROOT)
        raw = resolve_raw_dir()
        mats = sorted(Path(raw).glob("*.mat"))
        lines.append(f"ok   raw data {raw} ({len(mats)} .mat files)")
        if len(mats) < 2:
            ok = False
            lines.append("FAIL need at least 2 cells for leave-one-out")
    except Exception as err:
        ok = False
        lines.append(f"FAIL raw data: {err}")

    try:
        free_gb = shutil.disk_usage(ROOT).free / 1e9
        lines.append(f"ok   free disk {free_gb:.1f} GB")
        if free_gb < 1:
            ok = False
            lines.append("FAIL less than 1 GB free")
    except OSError:
        pass

    return ok, lines


def backup(stamp):
    dest = ROOT / "artifacts" / f"prev_{stamp}"
    dest.mkdir(parents=True, exist_ok=True)

    for src in (MODELS, PROC):
        if src.exists():
            shutil.copytree(src, dest / src.name)

    return dest


def run_stage(name, script, log_dir, env):
    log = log_dir / f"{name}.log"
    t0 = time.time()

    with open(log, "w") as fh:
        proc = subprocess.run(
            [sys.executable, script],
            cwd=ROOT,
            env=env,
            stdout=fh,
            stderr=subprocess.STDOUT,
        )

    return {
        "stage": name,
        "ok": proc.returncode == 0,
        "seconds": round(time.time() - t0, 1),
        "log": str(log.relative_to(ROOT)),
    }


def gates(mode):
    """Check results. Returns list of (name, ok, detail)."""

    import numpy as np
    import pandas as pd

    out = []
    lim = LIMITS[mode]

    for rel in ARTIFACTS:
        out.append(
            (f"artifact {rel}", (ROOT / rel).exists(), "exists")
        )

    try:
        nested = pd.read_csv(PROC / "soh_nested_results.csv")
        mae = float(nested["MAE"].mean())
        worst = float(nested["MAE"].max())
        out.append((
            "SOH nested LOCO mean MAE",
            mae <= lim["soh_mae"],
            f"{mae:.2f}% (limit {lim['soh_mae']}, verified 3.47 full)",
        ))
        out.append((
            "SOH worst cell MAE",
            worst <= lim["soh_worst_cell"],
            f"{worst:.2f}% (limit {lim['soh_worst_cell']}, "
            "verified 7.09 on B0006)",
        ))
    except Exception as err:
        out.append(("SOH results readable", False, str(err)))

    try:
        traj = pd.read_csv(PROC / "rul_trajectory_results.csv")
        t = float(traj["trajectory_mae"].mean())
        b = float(traj["fleet_lifetime_baseline_mae"].mean())
        out.append((
            "RUL trajectory beats lifetime baseline (mean)",
            t < b,
            f"{t:.1f} vs {b:.1f} discharges (verified 21.0 vs 32.7)",
        ))
    except Exception as err:
        out.append(("RUL results readable", False, str(err)))

    try:
        det = pd.read_csv(PROC / "pack_detection_summary.csv")
        det = det.set_index("fault")
        for fault in ("resistance_spike", "thermal"):
            rate = float(det.loc[fault, "rule_rate"])
            out.append((
                f"pack rule detection {fault}",
                rate >= 0.9,
                f"{rate:.0%} (verified 100%)",
            ))
        fa = pd.read_csv(PROC / "pack_false_alarms.csv")
        false = int(
            fa["false_resistance_spike"].sum()
            + fa["false_thermal"].sum()
        )
        out.append((
            "pack false resistance/thermal alarms on clean data",
            false == 0,
            f"{false} (verified 0)",
        ))
    except Exception as err:
        out.append(("pack results readable", False, str(err)))

    try:
        import joblib
        bundle = joblib.load(MODELS / "soh_xgb_all.joblib")
        df = pd.read_csv(PROC / "nasa_ml_dataset.csv")
        pred = bundle["model"].predict(df[bundle["features"]])
        err = float(np.mean(np.abs(pred - df[bundle["target"]])))
        out.append((
            "soh_xgb_all loads and predicts (in-sample MAE, sanity)",
            err < 2.5 and np.isfinite(pred).all(),
            f"{err:.2f}% over {len(df)} cycles",
        ))
        from evaluation import LEAKY_FEATURES
        leaked = (set(LEAKY_FEATURES) | {"soh_pct", "rul"}) & set(bundle["features"])
        out.append((
            "no target-derived feature in the SOH model",
            not leaked,
            f"{len(bundle['features'])} features",
        ))
    except Exception as err:
        out.append(("model loads", False, str(err)))

    return out


def write_report(stamp, mode, n_iter, pre, results, gate_rows, prev):
    rep_dir = ROOT / "reports"
    rep_dir.mkdir(exist_ok=True)
    total = sum(r["seconds"] for r in results)
    passed = all(r["ok"] for r in results) and all(
        g[1] for g in gate_rows
    )

    data = {
        "timestamp": stamp,
        "mode": mode,
        "n_iter": n_iter,
        "passed": passed,
        "total_seconds": round(total, 1),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "stages": results,
        "gates": [
            {"name": n, "ok": bool(o), "detail": d}
            for n, o, d in gate_rows
        ],
        "previous_artifacts_backup": str(prev.relative_to(ROOT)),
    }

    (rep_dir / f"train_report_{stamp}.json").write_text(
        json.dumps(data, indent=2)
    )

    md = [
        f"# Training report {stamp}",
        "",
        f"Result: **{'PASS' if passed else 'FAIL'}**  |  mode: {mode}"
        f"  |  search iterations: {n_iter}"
        f"  |  total {total / 60:.1f} min",
        "",
        "## Stages",
        "",
        "| stage | ok | seconds | log |",
        "|---|---|---|---|",
    ]
    for r in results:
        md.append(
            f"| {r['stage']} | {'yes' if r['ok'] else 'NO'} | "
            f"{r['seconds']} | {r['log']} |"
        )
    md += ["", "## Gates", "", "| check | ok | detail |", "|---|---|---|"]
    for n, o, d in gate_rows:
        md.append(f"| {n} | {'yes' if o else 'NO'} | {d} |")
    md += [
        "",
        f"Previous models and results were copied to "
        f"`{prev.relative_to(ROOT)}`. To roll back, copy its `models` "
        "and `processed` folders over `models/` and `data/processed/`.",
        "",
    ]
    path = rep_dir / f"train_report_{stamp}.md"
    path.write_text("\n".join(md))

    return passed, path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--quick", action="store_true",
                    help="3 search iterations instead of 20 (smoke test)")
    ap.add_argument("--n-iter", type=int, default=None,
                    help="override hyperparameter search iterations")
    ap.add_argument("--check", action="store_true",
                    help="preflight only")
    ap.add_argument("--only", action="append", default=None,
                    help="run only the named stage (repeatable)")
    ap.add_argument("--no-backup", action="store_true",
                    help="do not copy models/results before training")
    args = ap.parse_args(argv)

    mode = "quick" if args.quick else "full"
    n_iter = args.n_iter if args.n_iter is not None else (3 if args.quick else 20)
    if n_iter < 1:
        ap.error("--n-iter must be positive")

    ok, lines = preflight()
    print("PREFLIGHT")
    print("\n".join("  " + x for x in lines))

    if not ok:
        print("\nPreflight failed. Fix the lines marked FAIL.")
        return 2

    if args.check:
        print("\nPreflight passed.")
        return 0

    names = [n for n, _ in STAGES]
    if args.only:
        bad = set(args.only) - set(names)
        if bad:
            print(f"unknown stages {sorted(bad)}; known {names}")
            return 2

    chosen = [
        (n, s) for n, s in STAGES
        if not args.only or n in args.only
    ]

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = ROOT / "logs" / f"train_{stamp}"
    log_dir.mkdir(parents=True, exist_ok=True)

    if args.no_backup:
        prev = ROOT / "artifacts" / "none"
        prev.mkdir(parents=True, exist_ok=True)
    else:
        prev = backup(stamp)

    env = dict(os.environ)
    env["SGE_N_ITER"] = str(n_iter)
    env["PYTHONUNBUFFERED"] = "1"

    print(f"\nMODE {mode}, search iterations {n_iter}")
    print(f"logs: {log_dir.relative_to(ROOT)}\n")

    results = []
    for name, script in chosen:
        print(f"[{name}] running ...", flush=True)
        r = run_stage(name, script, log_dir, env)
        results.append(r)
        print(
            f"[{name}] {'ok' if r['ok'] else 'FAILED'} "
            f"in {r['seconds']} s",
            flush=True,
        )
        if not r["ok"]:
            print(f"\nStage failed. Read {r['log']}")
            break

    gate_rows = gates(mode) if all(r["ok"] for r in results) else []
    if args.only:
        gate_rows.append(("full pipeline executed", False,
                          "--only is diagnostic; existing outputs are not proof of retraining"))
    passed, path = write_report(
        stamp, mode, n_iter, lines, results, gate_rows, prev
    )

    print("\nGATES")
    for n, o, d in gate_rows:
        print(f"  {'ok  ' if o else 'FAIL'} {n}: {d}")

    print(f"\n{'PASS' if passed else 'FAIL'}. Report: {path}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
