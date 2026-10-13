#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMPDIR=$(mktemp -d)

cleanup() {
    rm -rf "$TMPDIR"
}
trap cleanup EXIT

cp -r "$ROOT"/* "$TMPDIR/" 2>/dev/null || true
cp -r "$ROOT"/.??* "$TMPDIR/" 2>/dev/null || true

cd "$TMPDIR"
bash scripts/download_dataset.sh
python run_pipeline.py --skip-train
python -m pytest tests -q
