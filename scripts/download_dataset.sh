#!/usr/bin/env bash
# Download and verify NASA Ames Prognostics Battery Aging Dataset (B0005 to B0056)
#
# The NASA archive is a zip that contains six more zips, and those hold
# the .mat files. Both levels are extracted here. Every file listed in
# scripts/checksums.sha256 must be present and match, or the script exits
# non-zero.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
TARGET_DIR="$ROOT_DIR/BMS Dataset/Battery_DataSet/Battery_DataSet"
CHECKSUMS_FILE="$SCRIPT_DIR/checksums.sha256"
URL="https://phm-datasets.s3.amazonaws.com/NASA/5.+Battery+Data+Set.zip"
WORK_DIR="$ROOT_DIR/data/tmp_download"

echo "=== NASA Battery Dataset Downloader & Verifier ==="

if [ ! -f "$CHECKSUMS_FILE" ]; then
    echo "ERROR: checksum file not found at $CHECKSUMS_FILE" >&2
    exit 1
fi

verify() {
    # Without --ignore-missing, a listed file that is absent is a failure.
    (cd "$TARGET_DIR" && sha256sum --check --quiet "$CHECKSUMS_FILE")
}

mkdir -p "$TARGET_DIR"

if verify 2>/dev/null; then
    echo "All files already present and verified in $TARGET_DIR"
    exit 0
fi

mkdir -p "$WORK_DIR"
trap 'rm -rf "$WORK_DIR"' EXIT

echo "Downloading dataset from $URL ..."
curl -fSL "$URL" -o "$WORK_DIR/outer.zip"

echo "Extracting archive (outer zip, then the zips inside it) ..."
python3 - "$WORK_DIR" <<'PY'
import sys
import zipfile
from pathlib import Path

work = Path(sys.argv[1])
outer = work / "outer.zip"
extracted = work / "extracted"
with zipfile.ZipFile(outer) as z:
    z.extractall(extracted)
inner = sorted(extracted.rglob("*.zip"))
if not inner:
    raise SystemExit("ERROR: no inner zip files found in the NASA archive")
for zp in inner:
    with zipfile.ZipFile(zp) as z:
        z.extractall(extracted / "mat" / zp.stem)
PY

n=0
while IFS= read -r -d '' f; do
    cp "$f" "$TARGET_DIR/"
    n=$((n + 1))
done < <(find "$WORK_DIR/extracted" -name "*.mat" -print0)
echo "Copied $n .mat files to $TARGET_DIR"
if [ "$n" -eq 0 ]; then
    echo "ERROR: no .mat files found after extraction" >&2
    exit 1
fi

echo "Verifying SHA256 checksums ..."
verify
echo "Checksum verification succeeded: all files in $(basename "$CHECKSUMS_FILE") match."
