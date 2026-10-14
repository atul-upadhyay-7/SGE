#!/usr/bin/env bash
#
# verify.sh — reproducible end-to-end verification.
#
# Clones the committed repository into a temporary directory (it does
# NOT copy the working tree, so only committed state is verified),
# downloads and checksums the NASA dataset, runs the pipeline, runs
# the test suite, checks that every number in README.md / CHANGES.md
# is backed by a CSV, and checks that those documents are in sync with
# the generator.
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
echo ">>> checking numbers in README.md / CHANGES.md"
"$PYTHON" scripts/check_numbers.py

echo
echo ">>> checking generated documents are in sync"
if git diff --quiet -- README.md CHANGES.md; then
    echo "    README.md and CHANGES.md match the generator output"
else
    echo "    ERROR: committed documents differ from generated output:"
    git --no-pager diff --stat -- README.md CHANGES.md
    exit 1
fi

echo
echo "============================================================"
echo "verify.sh: ALL CHECKS PASSED"
echo "============================================================"
