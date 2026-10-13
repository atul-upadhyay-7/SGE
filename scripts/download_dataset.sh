#!/usr/bin/env bash
# Download and verify NASA Ames Prognostics Battery Aging Dataset (B0005 to B0056)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
TARGET_DIR="$ROOT_DIR/BMS Dataset/Battery_DataSet/Battery_DataSet"
CHECKSUMS_FILE="$SCRIPT_DIR/checksums.sha256"
URL="https://phm-datasets.s3.amazonaws.com/NASA/5.+Battery+Data+Set.zip"
TMP_ZIP="$ROOT_DIR/data/5_Battery_Data_Set.zip"

echo "=== NASA Battery Dataset Downloader & Verifier ==="
mkdir -p "$TARGET_DIR"

if [ -f "$TARGET_DIR/B0005.mat" ] && [ -f "$TARGET_DIR/B0006.mat" ] && [ -f "$TARGET_DIR/B0007.mat" ] && [ -f "$TARGET_DIR/B0018.mat" ]; then
    echo "Files already present in $TARGET_DIR. Verifying checksums..."
else
    echo "Downloading dataset from $URL..."
    curl -fSL "$URL" -o "$TMP_ZIP"
    echo "Extracting archive..."
    unzip -q -o "$TMP_ZIP" -d "$ROOT_DIR/data/tmp_extracted"
    # Move .mat files into place
    find "$ROOT_DIR/data/tmp_extracted" -name "*.mat" -exec cp {} "$TARGET_DIR/" \;
    rm -rf "$ROOT_DIR/data/tmp_extracted" "$TMP_ZIP"
fi

echo "Verifying SHA256 checksums..."
cd "$TARGET_DIR"
if [ -f "$CHECKSUMS_FILE" ]; then
    sha256sum --check --ignore-missing "$CHECKSUMS_FILE"
    echo "Checksum verification succeeded!"
else
    echo "Warning: Checksum file not found at $CHECKSUMS_FILE"
fi
