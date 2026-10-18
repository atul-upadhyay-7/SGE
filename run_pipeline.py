#!/usr/bin/env python3
"""
run_pipeline.py
---------------
Run the whole battery-PDM pipeline end to end from a checkout.

Order matters: each stage consumes the previous stage's
artifacts, and the SOH model artifact embeds the feature list,
so training must follow any change to src/features.py.

Stages
   1. load_data        .mat -> cycle-level feature CSV
   2. preprocessing    targets and the ML dataset
   3. data_cleaning    cleaned ML dataset + per-cell audit
   4. random_load      do B0038-B0044 reach 80%? (count, no model)
   5. random_load_transfer  does the window model transfer to them?
   6. window_robustness  does it survive noise and shorter windows?
   7. window_partial   how partial is the window? (per cell + mean)
   8. window_train     a model trained on each candidate window
   9. data_quality     audit, fails loudly on a bad table
  10. train_soh        SOH models and artifacts
  11. train_rul        RUL models and artifacts
  12. rul_trajectory   RUL trajectories, bands and method disagreement
  13. rul_cross_group  RUL on the random-load groups
  14. run_experiments  formulas, ablation, leave-one-group-out
  15. pack_evaluate    pack alert engine on synthetic faults
  16. anomaly          cycle screening
  17. benchmark_latency inference latency
  18. make_report      README/CHANGES numbers from the CSVs
  19. make_alerts      Grafana alert rules from the training range
  20. load_to_sqlite   dashboard database
  21. pytest           the full suite

Any stage that exits non-zero stops the run. Nothing is
swallowed: the failing stage, its command and its own output
are what you see.

Usage
   python run_pipeline.py                 # everything
   python run_pipeline.py --skip-train    # keep existing models
   python run_pipeline.py --only train_soh

Note: train_soh runs a nested leave-one-cell-out
hyperparameter search and takes tens of minutes on four
cells. Use --skip-train when the artifacts are already
current.

Reproducibility: --skip-train must leave `git status` clean.
Training-dependent outputs are therefore not rewritten on a
skip-train run: benchmark_latency is skipped entirely (its
timings measure the trained artifacts and jitter by nature),
and pack_evaluate is run with --no-save-model so the committed
models/pack_iforest.joblib stays untouched. Every CSV writer
rounds floats to 10 decimals so reruns stay byte-identical.
"""

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent

# (name, command)
STAGES = [
    ("load_data", [sys.executable, "src/load_data.py"]),
    ("preprocessing", [sys.executable, "src/preprocessing.py"]),
    ("data_cleaning", [sys.executable, "src/data_cleaning.py"]),
    ("random_load", [sys.executable, "src/random_load.py"]),
    (
        "random_load_transfer",
        [sys.executable, "src/random_load_transfer.py"],
    ),
    ("window_robustness", [sys.executable, "src/window_robustness.py"]),
    ("window_partial", [sys.executable, "src/window_partial.py"]),
    ("window_train", [sys.executable, "src/window_train_sweep.py"]),
    ("data_quality", [sys.executable, "src/data_quality.py"]),
    ("train_soh", [sys.executable, "src/train_soh.py"]),
    ("train_rul", [sys.executable, "src/train_rul.py"]),
    ("rul_trajectory", [sys.executable, "src/rul_trajectory.py"]),
    ("rul_cross_group", [sys.executable, "src/rul_cross_group.py"]),
    ("run_experiments", [sys.executable, "src/run_experiments.py"]),
    ("pack_evaluate", [sys.executable, "src/pack/evaluate.py"]),
    ("anomaly", [sys.executable, "src/anomaly.py"]),
    (
        "benchmark_latency",
        [sys.executable, "src/benchmark_latency.py"],
    ),
    ("make_report", [sys.executable, "scripts/make_report.py"]),
    ("make_alerts", [sys.executable, "scripts/make_alert_rules.py"]),
    (
        "load_to_sqlite",
        [sys.executable, "dashboard/load_to_sqlite.py"],
    ),
    (
        "pytest",
        [sys.executable, "-m", "pytest", "-q"],
    ),
]

TRAIN_STAGES = {"train_soh", "train_rul"}

# Stages whose committed output only changes when models are
# (re)trained, so a --skip-train run must not rewrite them:
# benchmark_latency re-measures the trained artifacts and its
# wall-clock timings jitter between runs by nature.
TRAIN_ONLY_OUTPUTS = {"benchmark_latency"}


def run_stage(name, command):

    print(f"\n{'=' * 60}")
    print(f"STAGE: {name}")
    print(f"CMD  : {' '.join(command)}")
    print("=" * 60, flush=True)

    completed = subprocess.run(command, cwd=ROOT)

    if completed.returncode != 0:

        print(
            f"\nstage {name!r} FAILED with exit code "
            f"{completed.returncode}.\n"
            f"command: {' '.join(command)}\n"
            f"rerun the stage alone and read its output above.",
            file=sys.stderr,
        )

        return False

    return True


def main():

    parser = argparse.ArgumentParser(
        description="Run the battery-PDM pipeline."
    )

    parser.add_argument(
        "--skip-train",
        action="store_true",
        help="skip the two training stages (keep existing models)",
    )

    parser.add_argument(
        "--only",
        action="append",
        default=None,
        help="run only the named stage (repeatable)",
    )

    parser.add_argument(
        "--no-tests",
        action="store_true",
        help="skip the pytest stage",
    )

    args = parser.parse_args()

    stages = STAGES

    if args.only:

        wanted = set(args.only)

        unknown = wanted - {name for name, _ in STAGES}

        if unknown:

            parser.error(
                f"unknown stages: {sorted(unknown)}. "
                f"known: {[n for n, _ in STAGES]}"
            )

        stages = [
            (name, command)
            for name, command in STAGES
            if name in wanted
        ]

    elif args.skip_train:

        stages = [
            (name, command)
            for name, command in stages
            if name not in TRAIN_STAGES | TRAIN_ONLY_OUTPUTS
        ]

        # pack_evaluate still runs (its CSVs are training-independent)
        # but must not rewrite the committed Isolation Forest artifact.
        stages = [
            (name, command + ["--no-save-model"])
            if name == "pack_evaluate" else (name, command)
            for name, command in stages
        ]

    if args.no_tests:

        stages = [
            (name, command)
            for name, command in stages
            if name != "pytest"
        ]

    for name, command in stages:

        if not run_stage(name, command):

            sys.exit(1)

    print("\nAll stages completed.")


if __name__ == "__main__":

    main()
