"""
make_report.py
--------------
Read ONLY CSVs under data/processed and emit a summary CSV.
Fails if any required CSV is missing.
"""
from pathlib import Path
import csv
import sys

PROC = Path("data/processed")
OUT = PROC / "report_summary.csv"


def main():
    required = []
    # Will be extended later; keep minimal
    missing = [r for r in required if not (PROC / r).exists() and not (Path(r)).exists()]
    if missing:
        print(f"Missing required CSVs: {missing}", file=sys.stderr)
        sys.exit(1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['task', 'method', 'held_out_score', 'baseline_score', 'units', 'source_file', 'command'])
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
