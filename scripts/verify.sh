#!/usr/bin/env bash
#
# verify.sh — reproducible end-to-end verification.
#
# Clones the committed repository into a temporary directory (it does
# NOT copy the working tree, so only committed state is verified),
# downloads and checksums the NASA dataset, runs the pipeline, runs
# the test suite, checks that every number in README.md / CHANGES.md
# is a placeholder rendered from data/processed/report_numbers.json,
# and checks that those documents are in sync with the generator.
#
# Any failing step exits non-zero.
#
# Usage:
#   bash scripts/verify.sh
#
# Environment:
#   PYTHON   interpreter to use (default: python3). It must have the
#            project dependencies installed.
#   PDM_REPO repository to clone (default: this checkout's own path, so
#            the committed HEAD is verified). Set to a URL to verify a
#            remote instead.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(dirname "$HERE")"
PYTHON="${PYTHON:-python3}"
REPO="${PDM_REPO:-$SOURCE_ROOT}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "============================================================"
echo "verify.sh"
echo "  repository : $REPO"
echo "  python     : $($PYTHON --version 2>&1)"
echo "  workdir    : $WORK"
echo "============================================================"

echo
echo ">>> cloning committed repository"
git clone --quiet "$REPO" "$WORK/clone"
cd "$WORK/clone"
echo "    HEAD: $(git rev-parse --short HEAD)"

echo
echo ">>> downloading and checksumming the NASA dataset"
bash scripts/download_dataset.sh

echo
echo ">>> running the pipeline (skip training)"
"$PYTHON" run_pipeline.py --skip-train

echo
echo ">>> checking the pipeline left the working tree clean"
if [ -z "$(git status --porcelain)" ]; then
    echo "    git status is clean after the pipeline"
else
    echo "    ERROR: the pipeline changed or created tracked files:"
    git --no-pager status --porcelain
    exit 1
fi

echo
echo ">>> running the test suite"
"$PYTHON" -m pytest -q

echo
echo ">>> running the backend partial-window estimator tests"
# --noconftest: the backend's app-level conftest imports the full
# FastAPI app (and its MQTT dependency), which this pure-ML test does
# not need and this interpreter does not install.
(
    cd backend
    "$PYTHON" -m pytest -q --noconftest tests/test_window_estimator.py
)

echo
echo ">>> checking numbers in README.md / CHANGES.md"
"$PYTHON" scripts/check_numbers.py

echo
echo ">>> checking generated documents are in sync"
if git diff --quiet -- README.md CHANGES.md data/processed/report_numbers.json; then
    echo "    README.md, CHANGES.md and report_numbers.json match the generator output"
else
    echo "    ERROR: committed documents differ from generated output:"
    git --no-pager diff --stat -- README.md CHANGES.md data/processed/report_numbers.json
    exit 1
fi

echo
echo "============================================================"
echo "verify.sh: ALL CHECKS PASSED"
echo "============================================================"
