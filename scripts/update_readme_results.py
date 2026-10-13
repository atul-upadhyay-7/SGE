from pathlib import Path
import csv

PROC = Path("data/processed")
OUT = PROC / "report_summary.csv"


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['task', 'method', 'held_out_score', 'baseline_score', 'units', 'source_file', 'command'])
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
