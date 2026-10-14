#!/usr/bin/env python
"""
check_numbers.py
----------------
Fail if a number quoted in the generated regions of README.md or
CHANGES.md is not backed by a CSV under data/processed/.

Only the regions delimited by

    <!-- make_report:NAME -->
    ...
    <!-- make_report:NAME:end -->

are checked, because everything else in those documents is prose.
A number passes when some numeric cell in some CSV matches it at
the precision it is printed with (so 2.59 matches 2.5899...). This
catches a value that was hand-edited into the document after the
report was generated, and it catches a metric that was invented
rather than measured.
"""

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROC = ROOT / "data" / "processed"

DOCS = [ROOT / "README.md", ROOT / "CHANGES.md"]

REGION = re.compile(
    r"<!-- make_report:[a-z_]+ -->(.*?)<!-- make_report:[a-z_]+:end -->",
    re.DOTALL,
)

# A number not glued to a letter or another digit on either side, so
# cell ids (B0005), group ids (G1) and ordinals (10th) are ignored.
NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w])")


def collect_regions(text):
    return [match.group(1) for match in REGION.finditer(text)]


def load_values():
    values = []

    if not PROC.exists():
        return values

    for path in sorted(PROC.rglob("*.csv")):
        try:
            with path.open(newline="") as handle:
                for row in csv.reader(handle):
                    for cell in row:
                        cell = cell.strip()
                        try:
                            values.append(float(cell))
                        except ValueError:
                            continue
        except OSError:
            continue

    return values


def decimals(token):
    if "." in token:
        return len(token.split(".")[1])
    return 0


def matches(token, values):
    value = float(token)
    place = decimals(token)
    for candidate in values:
        if round(candidate, place) == value:
            return True
    return False


def main():
    regions = []

    for doc in DOCS:
        if not doc.exists():
            print(f"MISSING DOCUMENT: {doc}")
            return 1
        for index, region in enumerate(collect_regions(doc.read_text())):
            regions.append((doc.name, index, region))

    if not regions:
        print("no marked regions found — nothing to check")
        return 1

    values = load_values()

    if not values:
        print("no numeric values found under data/processed/")
        return 1

    failures = []

    for name, index, region in regions:
        for token in NUMBER.findall(region):
            if not matches(token, values):
                failures.append((name, index, token))

    total = sum(
        len(NUMBER.findall(region)) for _, _, region in regions
    )

    if failures:
        print(f"UNBACKED NUMBERS ({len(failures)} of {total}):")
        for name, index, token in failures:
            print(f"  {name} region {index}: {token}")
        return 1

    print(
        f"all {total} quoted numbers in {len(regions)} region(s) "
        f"are backed by data/processed CSVs"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
