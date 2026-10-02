"""
data_quality.py
---------------
Data audit for the processed SOH/RUL dataset.

This is not cosmetic. The pipeline must fail loudly if a
mandatory column is absent or malformed, because every
downstream number (SOH target, features, model metrics) is
only meaningful if the table it was computed from is the
table we think it is.

Writes two artifacts:

    data/processed/data_quality_report.csv
        Flat metric,value rows, for a quick look.

    data/processed/data_quality_report.json
        Full structured report, for programmatic checks and
        for the summary in the README.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd


PROCESSED_DIR = Path("data/processed")

ML_DATASET = PROCESSED_DIR / "nasa_ml_dataset.csv"

REPORT_CSV = PROCESSED_DIR / "data_quality_report.csv"

REPORT_JSON = PROCESSED_DIR / "data_quality_report.json"


# Columns without which the SOH pipeline cannot be trusted to
# run at all. Missing any of these is a hard error, not a
# warning.
MANDATORY_COLUMNS = [
    "cell_id",
    "cycle",
    "capacity_ah",
    "soh",
]

# Features whose ranges are worth watching for a units or
# parsing regression.
RANGE_CHECKED = [
    "voltage_mean",
    "voltage_min",
    "voltage_max",
    "voltage_std",
    "voltage_range",
    "current_mean",
    "current_std",
    "temperature_mean",
    "temperature_max",
    "discharge_duration_s",
    "voltage_drop",
    "resistance_proxy_ohm",
]

TARGETS = [
    "capacity_ah",
    "soh",
    "rul_cycles_80",
    "rul_cycles",
    "eol_cycle_threshold",
]


class DataQualityError(ValueError):
    """
    Raised when the processed dataset cannot be audited.

    Carries the file, the operation, the reason and a
    suggested fix, per the project's no-silent-failure rule.
    """


def _require(path):

    if not path.exists():

        raise DataQualityError(
            f"file: {path}\n"
            f"operation: read processed dataset\n"
            f"reason: file does not exist\n"
            f"fix: run  python src/load_data.py && "
            f"python src/preprocessing.py"
        )


def _require_columns(df, path):

    missing = [
        column
        for column in MANDATORY_COLUMNS
        if column not in df.columns
    ]

    if missing:

        raise DataQualityError(
            f"file: {path}\n"
            f"operation: validate mandatory columns\n"
            f"reason: missing mandatory columns: "
            f"{missing}\n"
            f"fix: rebuild the dataset with "
            f"python src/preprocessing.py. The schema "
            f"and the code have diverged."
        )


def _require_numeric(df, path):

    for column in MANDATORY_COLUMNS:

        if not pd.api.types.is_numeric_dtype(df[column]):

            if column == "cell_id":

                continue

            raise DataQualityError(
                f"file: {path}\n"
                f"operation: validate column types\n"
                f"reason: {column!r} is not numeric "
                f"(dtype {df[column].dtype})\n"
                f"fix: rebuild the dataset with "
                f"python src/preprocessing.py"
            )


def _finite_range(series):

    if series.notna().sum() == 0:

        return None

    return {
        "min": float(series.min()),
        "max": float(series.max()),
        "mean": float(series.mean()),
        "std": float(series.std()),
    }


def build_report(df):

    report = {}

    cells = sorted(df["cell_id"].unique().tolist())

    report["cells"] = cells
    report["n_cells"] = len(cells)
    report["n_rows"] = int(len(df))

    report["cycles_per_cell"] = (
        df.groupby("cell_id")["cycle"].count().to_dict()
    )

    # Missing values are reported, not silently imputed here.
    # Four missing capacity_change_ah / soh_change_pct values
    # are expected: the first cycle of each cell has no
    # predecessor. rul_cycles_80 is missing for post-EOL rows
    # by construction.
    report["missing"] = {
        column: int(count)
        for column, count in df.isna().sum().items()
        if count > 0
    }

    report["duplicate_rows"] = int(df.duplicated().sum())

    key = df[["cell_id", "cycle"]]

    report["duplicate_cycle_ids"] = int(
        key.duplicated().sum()
    )

    report["feature_ranges"] = {
        column: _finite_range(df[column])
        for column in RANGE_CHECKED
        if column in df.columns
    }

    report["target_ranges"] = {
        column: {
            **_finite_range(df[column]),
            "nan_count": int(df[column].isna().sum()),
        }
        for column in TARGETS
        if column in df.columns
    }

    # Outliers by the 1.5 IQR rule. Reported for awareness;
    # they are not removed, because a genuine deep-discharge
    # cycle is not an error.
    report["outliers_iqr"] = {}
    for column in RANGE_CHECKED:
        if column not in df.columns:
            continue
        q1 = df[column].quantile(0.25)
        q3 = df[column].quantile(0.75)
        iqr = q3 - q1
        if iqr <= 0:
            continue
        low = q1 - 1.5 * iqr
        high = q3 + 1.5 * iqr
        count = int(
            ((df[column] < low) | (df[column] > high)).sum()
        )
        report["outliers_iqr"][column] = count

    # Monotonicity. A Li-ion cell's capacity is expected to
    # trend down; a cycle that gains more than 1e-4 Ah against
    # its predecessor is a recovery or a measurement artefact
    # worth counting (B0005 is known to show one).
    monotonicity = {}
    degradation = {}

    for cell_id, group in df.groupby("cell_id"):

        group = group.sort_values("cycle")

        capacity = group["capacity_ah"].to_numpy()

        increases = int(
            (np.diff(capacity) > 1e-4).sum()
        )

        monotonicity[cell_id] = {
            "n_cycles": int(len(capacity)),
            "decreases": int((np.diff(capacity) < 0).sum()),
            "increases_gt_1e-4": increases,
            "monotonic_non_increasing": increases == 0,
        }

        cap0 = float(group["capacity_ah"].iloc[0])
        cap_end = float(group["capacity_ah"].iloc[-1])

        fade = (
            (cap0 - cap_end) / cap0 * 100
            if cap0 > 0
            else float("nan")
        )

        degradation[cell_id] = {
            "cap0": cap0,
            "cap_end": cap_end,
            "soh0": float(group["soh"].iloc[0]),
            "soh_end": float(group["soh"].iloc[-1]),
            "fade_pct_total": fade,
            "cycles": int(len(group)),
        }

    report["monotonicity_capacity"] = monotonicity
    report["degradation"] = degradation

    unusual = []
    insufficient = []

    for cell_id, stats in degradation.items():

        fade = stats["fade_pct_total"]

        if fade < 0:

            unusual.append({
                "cell": cell_id,
                "reason": "capacity_increased_overall",
                "fade_pct_total": fade,
            })

        if fade < 2 and stats["cycles"] > 50:

            insufficient.append({
                "cell": cell_id,
                "reason": "insufficient_fade",
                "fade_pct_total": fade,
            })

    report["unusual_degradation"] = unusual
    report["insufficient_degradation"] = insufficient

    soh_hist = pd.cut(
        df["soh"],
        bins=[0, 60, 70, 80, 85, 90, 95, 100]
    ).value_counts().sort_index()

    cycle_hist = pd.cut(
        df["cycle"], bins=10
    ).value_counts().sort_index()

    report["soh_distribution"] = {
        str(k): int(v) for k, v in soh_hist.items()
    }

    report["cycle_distribution"] = {
        str(k): int(v) for k, v in cycle_hist.items()
    }

    return report


def write_reports(report, report_csv, report_json):

    Path(report_json).write_text(
        json.dumps(report, indent=2, default=str)
    )

    rows = []

    def add(metric, value):

        rows.append({"metric": metric, "value": value})

    add("n_cells", report["n_cells"])
    add("n_rows", report["n_rows"])

    for cell_id, count in report["cycles_per_cell"].items():

        add(f"cycles_{cell_id}", count)

    for column, count in report["missing"].items():

        add(f"missing_{column}", count)

    add("duplicate_rows", report["duplicate_rows"])
    add(
        "duplicate_cycle_ids",
        report["duplicate_cycle_ids"]
    )

    for cell_id, stats in report["degradation"].items():

        add(
            f"fade_pct_total_{cell_id}",
            round(stats["fade_pct_total"], 4)
        )

    pd.DataFrame(rows).to_csv(report_csv, index=False)


def audit(path=ML_DATASET, report_dir=None):
    """
    Run the audit and write both reports.

    report_dir defaults to the dataset's own directory, so a
    call on a fixture file writes its reports next to that
    fixture rather than over the real
    data/processed/data_quality_report.*. Tests rely on this:
    an earlier version always wrote to the fixed production
    paths, so running the suite silently replaced the real
    report with a four-row fixture.

    Returns the structured report. Raises DataQualityError on
    a missing file, a missing mandatory column, or a
    non-numeric mandatory column.
    """

    path = Path(path)

    _require(path)

    # utf-8-sig tolerates a BOM, which pandas' default reader
    # would otherwise fold into the first column name and turn
    # every downstream lookup into a KeyError.
    df = pd.read_csv(path, encoding="utf-8-sig")

    _require_columns(df, path)
    _require_numeric(df, path)

    report = build_report(df)

    out_dir = (
        Path(report_dir)
        if report_dir is not None
        else path.parent
    )

    write_reports(
        report,
        out_dir / "data_quality_report.csv",
        out_dir / "data_quality_report.json",
    )

    return report


def main():

    report = audit()

    print("\n========== DATA QUALITY ==========")
    print(f"\nCells : {report['n_cells']}")
    print(f"Rows  : {report['n_rows']}")
    print(
        f"Duplicate cycle ids : "
        f"{report['duplicate_cycle_ids']}"
    )

    print("\nCycles per cell:")
    for cell_id, count in report["cycles_per_cell"].items():

        print(f"  {cell_id}: {count}")

    print("\nDegradation (total capacity fade):")
    for cell_id, stats in report["degradation"].items():

        print(
            f"  {cell_id}: "
            f"{stats['soh0']:.1f} -> "
            f"{stats['soh_end']:.1f} %SOH "
            f"({stats['fade_pct_total']:.2f}% fade)"
        )

    if report["unusual_degradation"]:

        print("\nUnusual degradation:")
        for item in report["unusual_degradation"]:

            print(f"  {item}")

    print(f"\nWrote {REPORT_CSV}")
    print(f"Wrote {REPORT_JSON}")


if __name__ == "__main__":

    main()
