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
   3. data_quality     audit, fails loudly on a bad table
   4. train_soh        SOH models and artifacts
   5. train_rul        RUL models and artifacts
   6. anomaly          cycle screening
   7. benchmark        inference latency
   8. load_to_sqlite   dashboard database
   9. pytest           the full suite

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
    ("data_quality", [sys.executable, "src/data_quality.py"]),
    ("train_soh", [sys.executable, "src/train_soh.py"]),
    ("train_rul", [sys.executable, "src/train_rul.py"]),
    ("rul_trajectory", [sys.executable, "src/rul_trajectory.py"]),
    ("pack_evaluate", [sys.executable, "src/pack/evaluate.py"]),
    ("anomaly", [sys.executable, "src/anomaly.py"]),
    (
        "benchmark_latency",
        [sys.executable, "src/benchmark_latency.py"],
    ),
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
            if name not in TRAIN_STAGES
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
