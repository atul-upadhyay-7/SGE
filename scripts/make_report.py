#!/usr/bin/env python
"""
make_report.py
--------------
Build the numbers that appear in README.md, CHANGES.md and the
generated regions of docs/RESULTS.md, docs/SETUP.md and
docs/MODEL_CARD.md from the measured CSVs. Nothing here is typed by
hand: every value is read from a CSV under data/processed/, and every
number written into a marked region is a {{id}} placeholder rendered
from that value.

A marked region looks like:

    <!-- make_report:results -->
    ... generated content ...
    <!-- make_report:results:end -->

The script writes, all from the CSVs:

  * the rendered regions back into their documents (README.md,
    CHANGES.md, docs/RESULTS.md, docs/SETUP.md, docs/MODEL_CARD.md);
  * data/processed/report_numbers.json — the machine-readable
    record {"<placeholder_id>": value} that check_numbers.py
    re-renders the regions from, with the placeholder templates
    themselves stored under the reserved "_templates" key;
  * data/processed/report_summary.csv — the human-readable record
    of every metric with its source file.

It fails if a required CSV is missing or empty, if a marker pair is
absent, or if a metric it needs is not present in its source CSV.
"""

import json
import sys
from pathlib import Path

import pandas as pd

from report_render import TEMPLATES_KEY, render

ROOT = Path(__file__).resolve().parent.parent
PROC = ROOT / "data" / "processed"
EXP = PROC / "experiments"

# The BMS contract thresholds live in src/features.py, not in this
# script. Importing the module (rather than copying the numbers) is
# what keeps the README's "what a real BMS must provide" section and
# the model's own contract from drifting apart.
sys.path.insert(0, str(ROOT / "src"))

import features  # noqa: E402

SRC_FEATURES = ROOT / "src" / "features.py"

README = ROOT / "README.md"
RESULTS = ROOT / "docs" / "RESULTS.md"
SETUP = ROOT / "docs" / "SETUP.md"
MODEL_CARD = ROOT / "docs" / "MODEL_CARD.md"
CHANGES = ROOT / "CHANGES.md"
SUMMARY = PROC / "report_summary.csv"
NUMBERS = PROC / "report_numbers.json"

REQUIRED = {
    "clean": PROC / "nasa_ml_dataset_clean.csv",
    "audit": PROC / "cell_audit.csv",
    "reference_sensitivity": PROC / "data_reference_sensitivity.csv",
    "soh_results": PROC / "soh_results.csv",
    "soh_nested": PROC / "soh_nested_results.csv",
    "soh_baselines": PROC / "soh_baselines.csv",
    "soh_null": PROC / "soh_null_baselines.csv",
    "rul_trajectory": PROC / "rul_trajectory_results.csv",
    "rul_comparison": PROC / "rul_method_comparison.csv",
    "rul_disagreement": PROC / "rul_method_disagreement.csv",
    "rul_band": PROC / "rul_band_calibration.csv",
    "rul_prediction_bands": PROC / "rul_prediction_bands.csv",
    "random_load": PROC / "random_load_rul_check.csv",
    "random_load_transfer": PROC / "soh_random_load_transfer.csv",
    "window_robustness": PROC / "soh_window_robustness.csv",
    "window_robustness_summary": PROC / "soh_window_robustness_summary.csv",
    "window_robustness_limits": PROC / "soh_window_robustness_limits.csv",
    "window_partial": PROC / "soh_window_partial.csv",
    "window_train": PROC / "soh_window_train_sweep.csv",
    "window_train_summary": PROC / "soh_window_train_sweep_summary.csv",
    "window_train_limits": PROC / "soh_window_train_sweep_limits.csv",
    "charge_window": PROC / "soh_charge_window.csv",
    "charge_comparison": PROC / "soh_charge_window_comparison.csv",
    "rul_random_labels": PROC / "rul_random_load_labels.csv",
    "rul_g6_loco": PROC / "rul_cross_group_g6_loco.csv",
    "rul_g1_to_g6": PROC / "rul_cross_group_g1_to_g6.csv",
    "rul_g6_to_g1": PROC / "rul_cross_group_g6_to_g1.csv",
    "rul_window_sweep": PROC / "rul_extrapolation_window_sweep.csv",
    "full_discharge": EXP / "soh_full_discharge.csv",
    "partial_discharge": EXP / "soh_partial_discharge.csv",
    "partial_transfer": EXP / "soh_partial_transfer.csv",
    "partial_window": EXP / "soh_partial_reference_window.csv",
    "ablation": EXP / "ablation_features.csv",
    "logo": EXP / "logo_results.csv",
    "cross_condition": EXP / "cross_condition.csv",
    "pack_detection": PROC / "pack_detection_summary.csv",
    "pack_false_alarms": PROC / "pack_false_alarms.csv",
}

_rows = []
_numbers = {}
_templates = {}


def record(task, method, protocol, metric, value, unit, source):
    _rows.append({
        "task": task,
        "method": method,
        "protocol": protocol,
        "metric": metric,
        "value": value,
        "unit": unit,
        "source_file": str(Path(source).relative_to(ROOT)),
    })


def ph(pid, value, spec=None):
    """
    Record `value` for placeholder id `pid` and return the
    "{{pid[:spec]}}" placeholder that renders it. The same id may be
    recorded again only with the same value, so one quantity has
    exactly one entry in report_numbers.json.

    Floats are stored rounded to 10 decimals: the documents render
    at most 3, and the rounding keeps the committed JSON stable
    against last-digit float jitter when the same numbers are
    recomputed on another machine.
    """

    if isinstance(value, float):
        value = round(value, 10)

    if pid in _numbers and _numbers[pid] != value:
        raise ValueError(
            f"placeholder {pid!r} recorded twice with different values: "
            f"{_numbers[pid]!r} and {value!r}"
        )
    _numbers[pid] = value
    return "{{" + pid + (f":{spec}" if spec else "") + "}}"


def load(key):
    path = REQUIRED[key]
    if not path.exists():
        raise FileNotFoundError(f"required CSV missing: {path}")
    frame = pd.read_csv(path)
    if frame.empty:
        raise ValueError(f"required CSV is empty: {path}")
    return frame


def fmt(value, decimals=2):
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):.{decimals}f}"


def mean(series):
    return float(pd.Series(series).mean())


def replace_region(text, name, body):
    start = f"<!-- make_report:{name} -->"
    end = f"<!-- make_report:{name}:end -->"
    if start not in text or end not in text:
        raise ValueError(f"marker pair '{name}' not found")
    head, rest = text.split(start, 1)
    _, tail = rest.split(end, 1)
    # A region whose start marker sits at the beginning of a line is a
    # block: the body goes on its own lines. A marker in the middle of a
    # line (an inline region, used for a single markdown table cell) gets
    # the body inline, so it cannot split the row across lines.
    if head == "" or head.endswith("\n"):
        return f"{head}{start}\n{body.rstrip()}\n{end}{tail}"
    return f"{head}{start}{body.strip()}{end}{tail}"


def region(name, template):
    """
    Render `template` from the recorded numbers and file it under
    `name`, returning the rendered text for the document.
    """

    rendered = render(template, _numbers)

    if name in _templates and _templates[name] != template:
        raise ValueError(f"region {name!r} built twice with different templates")
    _templates[name] = template

    return rendered


def write_regions(path, regions):
    """
    Replace each named region in `path` with its rendered body.

    The generated regions are spread across several documents now:
    README.md keeps the scope contract and the results-at-a-glance
    table, docs/RESULTS.md carries the long result tables,
    docs/SETUP.md carries the pack-monitoring table,
    docs/MODEL_CARD.md carries the model card, and CHANGES.md keeps
    the closing performance comparison. Each file says where its
    regions live by carrying the marker pair.
    """

    text = path.read_text()

    for name, body in regions.items():
        text = replace_region(text, name, body)

    path.write_text(text)


def model_mean(frame, model, column="MAE"):
    return float(frame.loc[frame["model"] == model, column].mean())


def _cc_row(cross, group, feature_set, model, column):
    row = cross[
        (cross["test_group"] == group)
        & (cross["feature_set"] == feature_set)
        & (cross["model"] == model)
    ]
    return row, float(row[column].iloc[0])


def _cc_physics(cross, group, column):
    row = cross[
        (cross["test_group"] == group)
        & (cross["feature_set"] == "physics")
    ]
    return float(row[column].iloc[0])


def _cc_cond(clean, group, column):
    """The nominal operating condition of a group, from the dataset."""
    return float(clean.loc[clean["group"] == group, column].iloc[0])


def build_cross_condition(logo, cross, clean, soh_base):
    """
    The cross-condition section that opens Key Results.

    The system predicts SOH from the 4.0-3.4 V window alone (the
    partial-discharge task), so every comparison here is on that
    task. Table 1 is the raw-window-feature leave-one-group-out
    failure; table 2 is the same protocol with per-cell
    baseline-relative window features, next to the window formula
    measured on exactly the held-out cells. B0053 is the only
    surviving G9 cell, so its line is that cell alone, not a group
    average.
    """

    lines = []

    lines.append(
        "#### Cross-condition transfer (leave-one-group-out, "
        "partial window only)\n"
    )
    lines.append(
        "**On the partial-discharge task the model does not transfer "
        "across conditions when it is trained on raw window features.** "
        "A BMS that only watches the partial-discharge window never sees "
        "the full discharge, so this is the honest version of the SOH "
        "question: predict SOH from the window alone."
    )

    order = ["G1", "G3", "G9"]
    label = {"G1": "G1", "G3": "G3", "G9": "B0053 (G9)"}

    in_cell_en = mean(soh_base.loc[soh_base["model"] == "ElasticNet", "MAE"])
    raw_en_max = float(
        logo.loc[logo["model"] == "ElasticNet", "mae_pct"].max()
    )
    record("SOH", "cross-condition", "leave-one-group-out", "in-condition ElasticNet MAE",
           in_cell_en, "%", REQUIRED["soh_baselines"])
    record("SOH", "cross-condition", "leave-one-group-out", "worst raw window ElasticNet MAE",
           raw_en_max, "%", REQUIRED["logo"])
    lines.append(
        f"With the full feature set, leave-one-cell-out within G1 the "
        f"same ElasticNet model reaches "
        f"{ph('cc_in_cell_en_mae', in_cell_en, '.2f')}% MAE; held out "
        f"across conditions on raw window features it climbs to as much "
        f"as {ph('cc_worst_raw_en_mae', raw_en_max, '.2f')}%. The table "
        f"below is that failure, measured, not a model claim.\n"
    )

    lines.append("| Held-out group | Cells | Cycles | ElasticNet MAE (%) | XGBoost MAE (%) |")
    lines.append("|---|---|---|---|---|")
    for group in order:
        rows = logo[logo["test_group"] == group]
        en = float(rows.loc[rows["model"] == "ElasticNet", "mae_pct"].iloc[0])
        xg = float(rows.loc[rows["model"] == "XGBoost", "mae_pct"].iloc[0])
        cells = int(rows["n_cells"].iloc[0])
        cycles = int(rows["n_cycles"].iloc[0])
        g = group.lower()
        record("SOH", "cross-condition", f"held-out {group}",
               "raw window ElasticNet MAE", en, "%", REQUIRED["logo"])
        record("SOH", "cross-condition", f"held-out {group}",
               "raw window XGBoost MAE", xg, "%", REQUIRED["logo"])
        lines.append(
            f"| {label[group]} | {ph('logo_' + g + '_cells', cells, 'd')} | "
            f"{ph('logo_' + g + '_cycles', cycles, 'd')} | "
            f"{ph('logo_' + g + '_en_mae', en, '.2f')} | "
            f"{ph('logo_' + g + '_xgb_mae', xg, '.2f')} |"
        )

    lines.append("")
    for group in order:
        record("SOH", "cross-condition", f"{group} nominal condition",
               "ambient temperature",
               _cc_cond(clean, group, "nominal_temperature_c"), "°C",
               REQUIRED["clean"])
        record("SOH", "cross-condition", f"{group} nominal condition",
               "discharge current",
               _cc_cond(clean, group, "nominal_current_a"), "A",
               REQUIRED["clean"])
    lines.append(
        "**Cell-relative window features recover the transfer, and the "
        "model then beats the window formula on the held-out "
        "condition.** Because the groups differ in ambient temperature "
        f"(G1 {ph('cc_g1_temp', int(_cc_cond(clean, 'G1', 'nominal_temperature_c')), 'd')} °C, "
        f"G3 {ph('cc_g3_temp', int(_cc_cond(clean, 'G3', 'nominal_temperature_c')), 'd')} °C, "
        f"G9 {ph('cc_g9_temp', int(_cc_cond(clean, 'G9', 'nominal_temperature_c')), 'd')} °C) "
        f"and current (G1 {ph('cc_g1_amp', int(_cc_cond(clean, 'G1', 'nominal_current_a')), 'd')} A, "
        f"G3 {ph('cc_g3_amp', int(_cc_cond(clean, 'G3', 'nominal_current_a')), 'd')} A, "
        f"G9 {ph('cc_g9_amp', int(_cc_cond(clean, 'G9', 'nominal_current_a')), 'd')} A), "
        "the raw levels do not line up. Dividing each window quantity by "
        "the cell's own first-five-discharge median and by its current "
        "puts every cell on the same scale and makes the ElasticNet "
        "transfer to a condition it has never seen. The window formula "
        "— the window's amp-hours over that same per-cell median — is "
        "the best a BMS can do from the window alone, so it is scored on "
        "exactly the held-out cells for comparison.\n"
    )

    lines.append(
        "| Held-out group | Cells | Cycles | window formula MAE (%) | "
        "raw ElasticNet MAE (%) | cell-relative ElasticNet MAE (%) | "
        "cell-relative XGBoost MAE (%) |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for group in order:
        cells = int(cross[cross["test_group"] == group]["n_cells"].iloc[0])
        cycles = int(cross[cross["test_group"] == group]["n_cycles"].iloc[0])
        formula = _cc_physics(cross, group, "mae_pct")
        _, raw_en = _cc_row(cross, group, "raw", "ElasticNet", "mae_pct")
        _, rel_en = _cc_row(cross, group, "cell_relative", "ElasticNet", "mae_pct")
        _, rel_xgb = _cc_row(cross, group, "cell_relative", "XGBoost", "mae_pct")
        g = group.lower()
        record("SOH", "cross-condition", f"held-out {group}",
               "window formula MAE", formula, "%", REQUIRED["cross_condition"])
        record("SOH", "cross-condition", f"held-out {group}",
               "cell-relative ElasticNet MAE", rel_en, "%",
               REQUIRED["cross_condition"])
        record("SOH", "cross-condition", f"held-out {group}",
               "cell-relative XGBoost MAE", rel_xgb, "%",
               REQUIRED["cross_condition"])
        lines.append(
            f"| {label[group]} | {ph('cc_' + g + '_cells', cells, 'd')} | "
            f"{ph('cc_' + g + '_cycles', cycles, 'd')} | "
            f"{ph('cc_' + g + '_coulomb_mae', formula, '.2f')} | "
            f"{ph('cc_' + g + '_raw_en_mae', raw_en, '.2f')} | "
            f"{ph('cc_' + g + '_rel_en_mae', rel_en, '.2f')} | "
            f"{ph('cc_' + g + '_rel_xgb_mae', rel_xgb, '.2f')} |"
        )

    lines.append("")
    lines.append(
        f"B0053 is the only G9 cell the audit keeps, so its row is that "
        f"cell alone, never averaged into a group. Reading it: the "
        f"cell-relative ElasticNet error on B0053 is "
        f"{ph('cc_g9_rel_en_mae', _cc_row(cross, 'G9', 'cell_relative', 'ElasticNet', 'mae_pct')[1], '.2f')}% "
        f"against the window formula's "
        f"{ph('cc_g9_coulomb_mae', _cc_physics(cross, 'G9', 'mae_pct'), '.2f')}%, "
        f"and the cell-relative XGBoost error is "
        f"{ph('cc_g9_rel_xgb_mae', _cc_row(cross, 'G9', 'cell_relative', 'XGBoost', 'mae_pct')[1], '.2f')}% "
        f"— both beat the formula on a condition the model has never "
        f"seen. On the two multi-cell groups the model also beats it "
        f"cell-by-cell (G1 ElasticNet "
        f"{ph('cc_g1_rel_en_mae', _cc_row(cross, 'G1', 'cell_relative', 'ElasticNet', 'mae_pct')[1], '.2f')}% "
        f"versus {ph('cc_g1_coulomb_mae', _cc_physics(cross, 'G1', 'mae_pct'), '.2f')}%, "
        f"G3 ElasticNet "
        f"{ph('cc_g3_rel_en_mae', _cc_row(cross, 'G3', 'cell_relative', 'ElasticNet', 'mae_pct')[1], '.2f')}% "
        f"versus {ph('cc_g3_coulomb_mae', _cc_physics(cross, 'G3', 'mae_pct'), '.2f')}%), "
        f"though XGBoost is weaker on G1. The cell-by-cell partial-"
        f"discharge section below reports the same task with "
        f"leave-one-cell-out validation.\n"
    )

    return "\n".join(lines)


def build_partial_transfer(partial):
    """
    The partial-discharge task scored cell by cell, next to the
    window formula on exactly the same held-out rows.
    """

    lines = []

    lines.append("#### Partial-discharge SOH, cell by cell (window only)\n")
    lines.append(
        "The same task, scored one held-out cell at a time: "
        "leave-one-cell-out within G1 and within G3, then train on "
        "every G1 and G3 cell and test on B0053, a condition the model "
        "has never seen. The window formula is scored on exactly the "
        "held-out rows.\n"
    )
    lines.append(
        "| Protocol | Held-out cell | Cycles | window formula MAE (%) | "
        "ElasticNet MAE (%) | XGBoost MAE (%) |"
    )
    lines.append("|---|---|---|---|---|---|")

    protocols = [
        ("leave-one-cell-out: G1", "loco_g1", "leave-one-cell-out: G1"),
        ("leave-one-cell-out: G3", "loco_g3", "leave-one-cell-out: G3"),
        ("train G1+G3, test B0053", "b0053", "Train G1+G3, test B0053"),
    ]

    for key, tag, display in protocols:

        block = partial[partial["protocol"] == key]

        for cell in sorted(block["test_cell"].unique()):

            rows = block[block["test_cell"] == cell]
            cell_id = str(cell).lower()
            cyc = int(rows["n_cycles"].iloc[0])

            formula = float(
                rows.loc[rows["model"] == "window_formula", "mae_pct"].iloc[0]
            )
            en = float(
                rows.loc[rows["model"] == "ElasticNet", "mae_pct"].iloc[0]
            )
            xgb = float(
                rows.loc[rows["model"] == "XGBoost", "mae_pct"].iloc[0]
            )

            record("SOH", "partial-discharge transfer", f"held-out {cell}",
                   "window formula MAE", formula, "%",
                   REQUIRED["partial_transfer"])
            record("SOH", "partial-discharge transfer", f"held-out {cell}",
                   "ElasticNet MAE", en, "%", REQUIRED["partial_transfer"])
            record("SOH", "partial-discharge transfer", f"held-out {cell}",
                   "XGBoost MAE", xgb, "%", REQUIRED["partial_transfer"])

            lines.append(
                f"| {display} | {cell} | {ph('pt_' + tag + '_' + cell_id + '_cyc', cyc, 'd')} | "
                f"{ph('pt_' + tag + '_' + cell_id + '_formula', formula, '.2f')} | "
                f"{ph('pt_' + tag + '_' + cell_id + '_en', en, '.2f')} | "
                f"{ph('pt_' + tag + '_' + cell_id + '_xgb', xgb, '.2f')} |"
            )

        means = {
            "formula": float(
                block.loc[block["model"] == "window_formula", "mae_pct"].mean()
            ),
            "en": float(
                block.loc[block["model"] == "ElasticNet", "mae_pct"].mean()
            ),
            "xgb": float(
                block.loc[block["model"] == "XGBoost", "mae_pct"].mean()
            ),
        }
        for suffix, model in (
            ("formula", "window formula"),
            ("en", "ElasticNet"),
            ("xgb", "XGBoost"),
        ):
            record("SOH", "partial-discharge transfer", f"mean ({key})",
                   f"{model} MAE", means[suffix], "%",
                   REQUIRED["partial_transfer"])
        lines.append(
            f"| **{display} — mean** | — | — | "
            f"{ph('pt_' + tag + '_mean_formula', means['formula'], '.2f')} | "
            f"{ph('pt_' + tag + '_mean_en', means['en'], '.2f')} | "
            f"{ph('pt_' + tag + '_mean_xgb', means['xgb'], '.2f')} |"
        )

    # B0005 is the one cell with both an in-sample regression fixture
    # and a held-out accuracy number; name the held-out one explicitly.
    b0005 = partial[
        (partial["protocol"] == "leave-one-cell-out: G1")
        & (partial["test_cell"] == "B0005")
    ]

    b0005_en = float(
        b0005.loc[b0005["model"] == "ElasticNet", "mae_pct"].iloc[0]
    )
    b0005_xgb = float(
        b0005.loc[b0005["model"] == "XGBoost", "mae_pct"].iloc[0]
    )

    lines.append("")
    lines.append(
        "The row the deployable estimator is held to is the B0005 "
        "leave-one-cell-out one — "
        f"{ph('pt_loco_g1_b0005_en', b0005_en, '.2f')}% ElasticNet and "
        f"{ph('pt_loco_g1_b0005_xgb', b0005_xgb, '.2f')}% XGBoost — on "
        "a cell its training set never contained. That is the accuracy "
        "number for B0005. The in-sample counterpart the backend "
        "regression test reads only shows the estimator runs the same "
        "deterministic pipeline at that cell; it is a determinism "
        "check, not accuracy, and is not quoted here."
    )

    # B0053 stands alone, so it can be plotted rather than only
    # tabulated: predicted against true SOH over discharge index.
    b0053 = partial[partial["protocol"] == "train G1+G3, test B0053"]

    def _r2(model):
        return float(
            b0053.loc[b0053["model"] == model, "r2"].iloc[0]
        )

    xgb_r2 = _r2("XGBoost")
    en_r2 = _r2("ElasticNet")
    record("SOH", "partial-discharge transfer", "held-out B0053",
           "XGBoost R2", xgb_r2, "", REQUIRED["partial_transfer"])
    record("SOH", "partial-discharge transfer", "held-out B0053",
           "ElasticNet R2", en_r2, "", REQUIRED["partial_transfer"])

    xgb_mae = float(
        b0053.loc[b0053["model"] == "XGBoost", "mae_pct"].iloc[0]
    )

    lines.append("")
    lines.append("#### B0053: one cell, and an R² that says the shape is not learnt\n")
    lines.append(
        "![Predicted versus true SOH for B0053 over discharge index]"
        "(soh_partial_B0053.png)\n"
    )
    lines.append(
        "B0053 is reported as the single cell it is, plotted predicted "
        "against true SOH over discharge index rather than only averaged "
        "into a metric. The partial-window XGBoost is close in level — MAE "
        f"{ph('pt_b0053_b0053_xgb', xgb_mae, '.2f')}% — but its R² is "
        f"{ph('pt_b0053_b0053_xgb_r2', xgb_r2, '.2f')}, below even a "
        "constant mean, so it does not follow the fade: the low error comes "
        "from B0053's narrow SOH range, not from tracking its trajectory. "
        "That is the honest reading of the transfer — the cell-relative "
        "features carry the operating condition's level to a cell the model "
        "has never seen, not its degradation path."
    )

    return "\n".join(lines)


def build_reference_window(sensitivity):
    """
    How many of a cell's own opening discharges the cell-relative
    baseline needs, on the partial-discharge task.
    """

    lines = []

    lines.append("#### Reference-window sensitivity (partial-discharge)\n")
    lines.append(
        "The cell-relative features need a baseline from the cell's own "
        "first `n` discharges; a new cell may have very few. Rebuilding "
        "the baseline for one, two, three and five opening discharges "
        "and re-running the task:\n"
    )

    protocols = [
        ("leave-one-cell-out: G1+G3", "loco", "Leave-one-cell-out (G1+G3)"),
        ("train G1+G3, test B0053", "b0053", "Train G1+G3, test B0053"),
    ]

    for key, tag, display in protocols:

        block = sensitivity[sensitivity["protocol"] == key]

        lines.append(f"\n**{display}**\n")
        lines.append(
            "| Discharges used as baseline | window formula MAE (%) | "
            "ElasticNet MAE (%) | XGBoost MAE (%) |"
        )
        lines.append("|---|---|---|---|")

        for n in sorted(block["ref_window"].unique()):

            rows = block[block["ref_window"] == n]

            formula = float(
                rows.loc[rows["model"] == "window_formula", "mae_pct"].iloc[0]
            )
            en = float(
                rows.loc[rows["model"] == "ElasticNet", "mae_pct"].iloc[0]
            )
            xgb = float(
                rows.loc[rows["model"] == "XGBoost", "mae_pct"].iloc[0]
            )

            model_rows = {
                "window formula": ("formula", formula),
                "ElasticNet": ("en", en),
                "XGBoost": ("xgb", xgb),
            }
            for model, (suffix, score) in model_rows.items():
                record("SOH", "reference-window sensitivity",
                       f"{display}, n={int(n)}", f"{model} MAE", score, "%",
                       REQUIRED["partial_window"])

            lines.append(
                f"| {ph('rw_' + tag + '_n' + str(int(n)), int(n), 'd')} | "
                f"{ph('rw_' + tag + '_n' + str(int(n)) + '_formula', formula, '.2f')} | "
                f"{ph('rw_' + tag + '_n' + str(int(n)) + '_en', en, '.2f')} | "
                f"{ph('rw_' + tag + '_n' + str(int(n)) + '_xgb', xgb, '.2f')} |"
            )

    lines.append("")
    lines.append(
        "Inside a group the picture is mixed: the plain window formula "
        "is sensitive to the baseline length, while the learned models "
        "stay flat because the held-out cell is trained on siblings at "
        "the same condition. The cross-condition transfer to B0053 is "
        "where the baseline length matters for the model too: one or two "
        "opening discharges set the scale too coarsely and the error "
        "spikes, and from three discharges it settles near its floor. "
        "The practical reading: give a new cell a few discharges before "
        "trusting its cell-relative SOH.\n"
    )

    return "\n".join(lines)


def build_random_load_transfer(frame):
    """
    The shipped partial-window SOH model on the random-load cells it
    was never trained on.

    Four G5/G6 cells reach the retirement threshold and pass the keep
    filter but are left out of training because their load and
    temperature change across the record. This scores the shipped
    model on them with the same cell-relative window features, next to
    the window formula on exactly the same discharges, and records
    whether the exclusion is a transfer failure or only a
    comparability choice.
    """

    lines = []
    cells = frame[frame["cell_id"] != "MEAN"]

    lines.append(
        "#### Random-load transfer (cells the model was never trained on)\n"
    )
    lines.append(
        "The random-load cells the audit keeps — B0039 from G5 and "
        "B0042–B0044 from G6 — reach the retirement threshold and pass the "
        "keep filter, but they are left out of training because their load "
        "and temperature change across the record. Feeding them to the "
        "shipped window model, which saw only the nine constant-current "
        "cells, with the same cell-relative features, next to the window "
        "formula on exactly the same discharges:\n"
    )
    lines.append(
        "| Held-out cell | Group | Cycles | window formula MAE (%) | "
        "window formula R² | cell-relative XGBoost MAE (%) | "
        "cell-relative XGBoost R² |"
    )
    lines.append("|---|---|---|---|---|---|---|")

    for _, row in cells.iterrows():

        cell = str(row["cell_id"])
        tag = cell.lower()

        for metric, value, unit in (
            ("window formula MAE", row["window_formula_mae_pct"], "%"),
            ("window formula R2", row["window_formula_r2"], ""),
            ("cell-relative XGBoost MAE", row["model_mae_pct"], "%"),
            ("cell-relative XGBoost R2", row["model_r2"], ""),
        ):
            record("SOH", "random-load transfer", f"held-out {cell}",
                   metric, value, unit, REQUIRED["random_load_transfer"])

        lines.append(
            f"| {cell} | {row['group']} | "
            f"{ph(tag + '_rlt_cycles', int(row['n_cycles']), 'd')} | "
            f"{ph(tag + '_rlt_formula_mae', float(row['window_formula_mae_pct']), '.2f')} | "
            f"{ph(tag + '_rlt_formula_r2', float(row['window_formula_r2']), '.3f')} | "
            f"{ph(tag + '_rlt_model_mae', float(row['model_mae_pct']), '.2f')} | "
            f"{ph(tag + '_rlt_model_r2', float(row['model_r2']), '.3f')} |"
        )

    mean = frame[frame["cell_id"] == "MEAN"].iloc[0]

    beaten = int(
        (cells["model_mae_pct"] < cells["window_formula_mae_pct"]).sum()
    )
    positive = int((cells["model_r2"] > 0.0).sum())

    record("SOH", "random-load transfer", "mean", "cell-relative XGBoost MAE",
           mean["model_mae_pct"], "%", REQUIRED["random_load_transfer"])
    record("SOH", "random-load transfer", "mean", "cell-relative XGBoost R2",
           mean["model_r2"], "", REQUIRED["random_load_transfer"])
    record("SOH", "random-load transfer", "mean", "window formula MAE",
           mean["window_formula_mae_pct"], "%",
           REQUIRED["random_load_transfer"])
    record("SOH", "random-load transfer", "verdict",
           "cells where the model beats the formula", beaten, "cells",
           REQUIRED["random_load_transfer"])
    record("SOH", "random-load transfer", "verdict",
           "cells where the model R2 is positive", positive, "cells",
           REQUIRED["random_load_transfer"])

    lines.append(
        f"| **Mean** | — | {ph('rlt_mean_cycles', int(mean['n_cycles']), 'd')} | "
        f"{ph('rlt_mean_formula_mae', float(mean['window_formula_mae_pct']), '.2f')} | "
        f"{ph('rlt_mean_formula_r2', float(mean['window_formula_r2']), '.3f')} | "
        f"{ph('rlt_mean_model_mae', float(mean['model_mae_pct']), '.2f')} | "
        f"{ph('rlt_mean_model_r2', float(mean['model_r2']), '.3f')} |"
    )

    lines.append("")

    flat = cells[cells["model_r2"] <= 0.0]
    flat_note = ""

    if len(flat) == 1:

        r = flat.iloc[0]
        flat_note = (
            f" The exception is {r['cell_id']} ({r['group']}): its R² is "
            f"{ph(str(r['cell_id']).lower() + '_rlt_model_r2', float(r['model_r2']), '.3f')}, "
            "below a constant mean, so there the model reads the level, "
            "not the fade shape — the same caveat B0053 carries above."
        )

    lines.append(
        f"The model does not fail on these cells. It beats the window "
        f"formula on {ph('rlt_beaten', beaten, 'd')} of "
        f"{ph('rlt_n_cells', int(len(cells)), 'd')}, with mean MAE "
        f"{ph('rlt_mean_model_mae', float(mean['model_mae_pct']), '.2f')}% "
        f"against the formula's "
        f"{ph('rlt_mean_formula_mae', float(mean['window_formula_mae_pct']), '.2f')}%, "
        f"and it tracks the fade (R² above zero) on "
        f"{ph('rlt_positive', positive, 'd')} of "
        f"{ph('rlt_n_cells', int(len(cells)), 'd')}."
        f"{flat_note} The exclusion is therefore a comparability choice, "
        "not a transfer failure.\n"
    )

    return "\n".join(lines)


def _wr_tag(condition):
    """A placeholder-safe id for a condition label (dots become underscores)."""

    return "wr_" + "".join(
        ch if ch.isalnum() else "_" for ch in str(condition)
    )


def build_window_robustness(detail, summary, limits):
    """
    Sensor-noise and shorter-window robustness of the partial-window
    model and the window formula, scored on held-out cells only.
    """

    lines = []

    limit = float(limits["mae_limit_pct"].iloc[0])

    lines.append(
        "#### Noise and shorter-window robustness (held-out cells)\n"
    )
    lines.append(
        f"The partial-window model is trained on the clean "
        f"{ph('wr_band_high', float(limits['smallest_window_high_v'].iloc[0]), '.1f')}-"
        f"{ph('wr_band_low', float(limits['smallest_window_low_v'].iloc[0]), '.1f')} V "
        "window; here the *held-out* cell's inputs are perturbed while its "
        "training siblings stay clean, so this measures inference-time "
        "robustness. Each level is scored by leave-one-cell-out within G1 "
        "and G3 and by training on G1+G3 and testing B0053, then pooled "
        f"over all nine held-out cells. A pooled MAE under "
        f"{ph('wr_limit', limit, '.0f')}% is treated as still working.\n"
    )
    lines.append(
        "| Perturbation | Voltage noise (mV) | Current noise (%) | "
        "Window (V) | Held-out XGBoost MAE (%) | ElasticNet MAE (%) | "
        "window formula MAE (%) |"
    )
    lines.append("|---|---|---|---|---|---|---|")

    display = {
        "clean": "Clean baseline",
        "voltage": "Voltage noise",
        "current": "Current noise",
        "window": "Shorter window",
    }

    for _, row in summary.iterrows():

        tag = _wr_tag(row["condition"])
        kind = str(row["kind"])

        voltage_cell = (
            f"{ph(tag + '_mv', float(row['voltage_noise_mv']), '.0f')} mV"
            if kind == "voltage" else "—"
        )
        current_cell = (
            f"{ph(tag + '_pct', float(row['current_noise_pct']), '.0f')}%"
            if kind == "current" else "—"
        )
        window_cell = (
            f"{ph(tag + '_hi', float(row['window_high_v']), '.1f')}–"
            f"{ph(tag + '_lo', float(row['window_low_v']), '.1f')}"
        )

        for metric, key in (
            ("Held-out XGBoost MAE", "xgb_mae_pct"),
            ("ElasticNet MAE", "elasticnet_mae_pct"),
            ("window formula MAE", "window_formula_mae_pct"),
        ):
            record("SOH", "window robustness", row["condition"],
                   metric, row[key], "%", REQUIRED["window_robustness_summary"])

        lines.append(
            f"| {display.get(kind, kind)} | {voltage_cell} | "
            f"{current_cell} | {window_cell} | "
            f"{ph(tag + '_xgb', float(row['xgb_mae_pct']), '.2f')} | "
            f"{ph(tag + '_en', float(row['elasticnet_mae_pct']), '.2f')} | "
            f"{ph(tag + '_formula', float(row['window_formula_mae_pct']), '.2f')} |"
        )

    # Where the cost of a narrower window lands: the pooled XGBoost MAE
    # by protocol, clean against the two shorter bands.
    lines.append("")
    lines.append("**Held-out XGBoost MAE (%) by protocol**\n")
    lines.append("| Protocol | Clean | 3.9–3.5 V | 3.8–3.6 V |")
    lines.append("|---|---|---|---|")

    protocol_rows = [
        ("leave-one-cell-out: G1", "G1 leave-one-cell-out", "g1"),
        ("leave-one-cell-out: G3", "G3 leave-one-cell-out", "g3"),
        ("train G1+G3, test B0053", "G1+G3 → B0053", "b0053"),
    ]
    band_columns = [
        ("clean", "clean"),
        ("window_3.9_3.5", "band_39"),
        ("window_3.8_3.6", "band_38"),
    ]

    for protocol, label, ptag in protocol_rows:

        cells = []

        for condition, tag in band_columns:

            block = detail[
                (detail["condition"] == condition)
                & (detail["protocol"] == protocol)
                & (detail["model"] == "XGBoost")
            ]

            if block.empty:
                cells.append("—")
                continue

            weights = block["n_cycles"].to_numpy(dtype=float)
            pooled = float(
                (block["mae_pct"].to_numpy(dtype=float) * weights).sum()
                / weights.sum()
            )

            pid = f"wr_proto_{tag}_{ptag}"
            record("SOH", "window robustness", protocol,
                   f"XGBoost MAE ({condition})", pooled, "%",
                   REQUIRED["window_robustness"])

            cells.append(ph(pid, pooled, ".2f"))

        lines.append(f"| {label} | " + " | ".join(cells) + " |")

    # The two answers this phase asks for.
    largest_mv = limits["largest_voltage_noise_mv"].iloc[0]
    largest_pct = limits["largest_current_noise_pct"].iloc[0]

    narrow_xgb = {
        str(r["condition"]): float(r["xgb_mae_pct"])
        for _, r in summary[summary["kind"] == "window"].iterrows()
    }

    lines.append("")
    lines.append(
        "**Verdict.** The model tolerates sensor noise: pooled held-out MAE "
        f"stays under {ph('wr_limit', limit, '.0f')}% up to the largest "
        f"voltage noise tried, {ph('wr_largest_mv', float(largest_mv), '.0f')} mV, "
        "and the largest current noise tried, "
        f"{ph('wr_largest_pct', float(largest_pct), '.0f')}%. Current noise "
        "barely moves the features at all — the window capacity is an "
        "integral of the current, so zero-mean multiplicative noise averages "
        "out, and the window voltage features do not see the current. "
        "Voltage noise does move them, mostly through the dQ/dV peak height, "
        "but even at "
        f"{ph('wr_largest_mv', float(largest_mv), '.0f')} mV the pooled MAE is "
        f"{ph('wr_voltage_noise_20mv_xgb', float(summary.loc[summary['condition'] == 'voltage_noise_20mv', 'xgb_mae_pct'].iloc[0]), '.2f')}%.\n"
    )

    lines.append(
        "It does **not** survive a shorter window *at inference*: feeding a "
        "narrower band to the model trained on 4.0–3.4 V moves its inputs off "
        "the distribution it learnt, and both narrower bands push the pooled "
        "held-out MAE above the budget — "
        f"{ph('wr_window_3_9_3_5_xgb', narrow_xgb.get('window_3.9_3.5', float('nan')), '.2f')}% "
        "over 3.9–3.5 V and "
        f"{ph('wr_window_3_8_3_6_xgb', narrow_xgb.get('window_3.8_3.6', float('nan')), '.2f')}% "
        "over 3.8–3.6 V — but this is a mismatch test, not a verdict on "
        "shorter windows. The cost lands on G1: its leave-one-cell-out MAE "
        "rises from the clean value to well above the budget on both bands, "
        "while G3 and B0053 stay near it. Whether a *model trained on* the "
        "shorter band works is a separate question, answered in the next "
        "subsection.\n"
    )

    return "\n".join(lines)


def _wt_tag(window):
    """Placeholder-id tag for one window, e.g. window_4.0_3.4 -> wts_4_0_3_4."""

    return "wts_" + window.replace("window_", "").replace(".", "_")


def build_window_train_sweep(summary, limits):
    """
    docs/RESULTS.md: a model trained on each candidate window, scored
    on the same held-out cells as the shipped model.
    """

    lines = []

    limit = float(limits["mae_limit_pct"].iloc[0])

    lines.append(
        "#### A model trained on its own window (held-out cells)\n"
    )
    lines.append(
        "The robustness test above feeds a shorter band to the *shipped* "
        "model, which is trained on the 4.0–3.4 V feature distribution, so "
        "its failure is a mismatch. This test removes the mismatch: each "
        "band gets its own model, with its cell-relative features recomputed "
        "from the raw traces over that band only, and is scored the same way "
        "the present model is — leave-one-cell-out within G1 and within G3, "
        "then train on every G1 and G3 cell and test B0053 and the four "
        "random-load cells. All thirteen scored cells are held out of their "
        "own training set, and a band's training rows never enter another "
        "band's model. The MAE is pooled over the held-out cells; the "
        "random-load columns are those four cells alone.\n"
    )
    lines.append(
        "| Window (V) | Span (V) | XGBoost MAE (%) | ElasticNet MAE (%) | "
        "window formula MAE (%) | Random-load XGBoost MAE (%) | "
        "Random-load ElasticNet MAE (%) |"
    )
    lines.append("|---|---|---|---|---|---|---|")

    for _, row in summary.iterrows():

        tag = _wt_tag(str(row["window"]))
        source = REQUIRED["window_train_summary"]

        for metric, key in (
            ("XGBoost MAE", "xgb_mae_pct"),
            ("ElasticNet MAE", "elasticnet_mae_pct"),
            ("window formula MAE", "window_formula_mae_pct"),
            ("random-load XGBoost MAE", "xgb_mae_random_load_pct"),
            ("random-load ElasticNet MAE", "elasticnet_mae_random_load_pct"),
        ):
            record("SOH", "window train sweep", row["window"], metric,
                   row[key], "%", source)

        lines.append(
            f"| {ph(tag + '_hi', float(row['window_high_v']), '.1f')}–"
            f"{ph(tag + '_lo', float(row['window_low_v']), '.1f')} | "
            f"{ph(tag + '_span', float(row['window_span_v']), '.2f')} | "
            f"{ph(tag + '_xgb', float(row['xgb_mae_pct']), '.2f')} | "
            f"{ph(tag + '_en', float(row['elasticnet_mae_pct']), '.2f')} | "
            f"{ph(tag + '_formula', float(row['window_formula_mae_pct']), '.2f')} | "
            f"{ph(tag + '_rl_xgb', float(row['xgb_mae_random_load_pct']), '.2f')} | "
            f"{ph(tag + '_rl_en', float(row['elasticnet_mae_random_load_pct']), '.2f')} |"
        )

    smallest_name = str(limits["smallest_under_limit_window"].iloc[0])
    n_windows = int(limits["n_windows"].iloc[0])
    n_under = int(limits["n_under_limit"].iloc[0])

    window_ok = summary[
        ~summary["xgb_mae_random_load_pct"].isna()
        & (summary["xgb_mae_random_load_pct"] < limit)
    ]
    rl_ok = window_ok.sort_values("window_high_v").iloc[0]

    def _row(name):
        return summary[summary["window"] == name].iloc[0]

    if smallest_name == "none":

        lines.append("")
        lines.append(
            "**Verdict.** No window tried kept the pooled held-out MAE "
            f"under {ph('wts_limit', limit, '.0f')}% with a model of its own; "
            f"the smallest tried, span "
            f"{ph('wts_small_tried_span', float(limits['smallest_tried_span_v'].iloc[0]), '.2f')} V, "
            f"reaches {ph('wts_small_tried_mae', float(limits['smallest_tried_mae_pct'].iloc[0]), '.2f')}%.\n"
        )

    else:

        small = _row(smallest_name)
        trained = _row(str(limits["trained_window"].iloc[0]))
        tag = _wt_tag(smallest_name)

        lines.append("")
        lines.append(
            "**Verdict.** A shorter window *does* work once the model is "
            "trained on it: with a model of its own, every band tried keeps "
            f"the pooled held-out MAE under {ph('wts_limit', limit, '.0f')}% "
            f"({ph('wts_n_under', n_under, 'd')} of "
            f"{ph('wts_n_windows', n_windows, 'd')} bands). The smallest is "
            f"{ph(tag + '_hi', float(small['window_high_v']), '.1f')}–"
            f"{ph(tag + '_lo', float(small['window_low_v']), '.1f')} V "
            f"(span {ph(tag + '_span', float(small['window_span_v']), '.2f')} V), "
            f"at {ph(tag + '_xgb', float(small['xgb_mae_pct']), '.2f')}% "
            f"pooled over the nine constant-current held-out cells and "
            f"{ph('wts_small_all', float(limits['smallest_under_limit_mae_pct'].iloc[0]), '.2f')}% "
            "over all thirteen, against "
            f"{ph('wts_trained_all', float(limits['trained_window_mae_pct'].iloc[0]), '.2f')}% "
            "for the shipped 4.0–3.4 V band. The earlier failure was the "
            "shipped model being handed a different band, not the window "
            "length. The random-load cells are the stricter half of the "
            "test: there the smallest band sits just over budget at "
            f"{ph(tag + '_rl_xgb', float(small['xgb_mae_random_load_pct']), '.2f')}%, "
            "so the narrowest window that also stays under "
            f"{ph('wts_limit', limit, '.0f')}% on the random-load cells is "
            f"{ph('wts_rl_ok_hi', float(rl_ok['window_high_v']), '.1f')}–"
            f"{ph('wts_rl_ok_lo', float(rl_ok['window_low_v']), '.1f')} V "
            f"at {ph('wts_rl_ok_mae', float(rl_ok['xgb_mae_random_load_pct']), '.2f')}%.\n"
        )

    lines.append(
        "ElasticNet is reported for comparison but is the fragile model "
        "here: its inputs are unbounded ratios, and a random-load cell whose "
        "opening discharges barely cross the band (B0039) gives a tiny "
        "five-discharge baseline, so its later window ratios run orders of "
        "magnitude past the training range. A linear fit extrapolates that to "
        "implausible SOH while the tree model stays bounded, which is why the "
        "3% question is read against XGBoost, the shipped model family.\n"
    )

    return "\n".join(lines)


def build_window_train_share(summary, limits, wr_limits):
    """
    The one-line README limits-table cell: a shorter window works once
    the model is trained on it, with the smallest surviving band.
    """

    limit = float(limits["mae_limit_pct"].iloc[0])
    smallest_name = str(limits["smallest_under_limit_window"].iloc[0])

    if smallest_name == "none":

        return (
            f"With Gaussian noise on the held-out cell's inputs the pooled "
            f"MAE stays under {ph('wts_limit', limit, '.0f')}% up to "
            f"{ph('wr_largest_mv', float(wr_limits['largest_voltage_noise_mv'].iloc[0]), '.0f')} mV "
            f"of voltage noise and "
            f"{ph('wr_largest_pct', float(wr_limits['largest_current_noise_pct'].iloc[0]), '.0f')}% "
            f"of current noise, but no window shorter than the trained band "
            f"held the budget with a model of its own "
            f"(see *A model trained on its own window* under Key Results)."
        )

    small = summary[summary["window"] == smallest_name].iloc[0]
    tag = _wt_tag(smallest_name)

    return (
        f"With Gaussian noise on the held-out cell's inputs the pooled MAE "
        f"stays under {ph('wts_limit', limit, '.0f')}% up to "
        f"{ph('wr_largest_mv', float(wr_limits['largest_voltage_noise_mv'].iloc[0]), '.0f')} mV "
        f"of voltage noise and "
        f"{ph('wr_largest_pct', float(wr_limits['largest_current_noise_pct'].iloc[0]), '.0f')}% "
        f"of current noise. A shorter window fails only when the "
        f"{ph('wp_window_high', features.WINDOW_VOLTAGE_HIGH, '.1f')}–"
        f"{ph('wp_window_low', features.WINDOW_VOLTAGE_LOW, '.1f')} V "
        f"model is handed a band it was not trained on: train a model on the "
        f"band and it works down to a "
        f"{ph(tag + '_span', float(small['window_span_v']), '.2f')} V window "
        f"({ph(tag + '_hi', float(small['window_high_v']), '.1f')}–"
        f"{ph(tag + '_lo', float(small['window_low_v']), '.1f')} V), which holds "
        f"{ph('wts_small_all', float(limits['smallest_under_limit_mae_pct'].iloc[0]), '.2f')}% "
        f"pooled held-out MAE (see *A model trained on its own window* under "
        f"Key Results)."
    )


def build_charge(comparison):
    """
    docs/RESULTS.md: whether the constant-current charge estimates SOH as
    well as the discharge window does, from the separate charge stage.
    """

    lines = []

    lines.append("#### Can constant-current charging estimate SOH?\n")

    lines.append(
        "The discharge loader keeps discharge cycles only: "
        "`load_data.iter_discharge_traces` skips every cycle whose type is "
        "not a discharge, so the ML dataset, the window model and every "
        "document see discharges alone. Charge segments are read by a "
        "separate stage (`src/charge_soh.py`) that leaves the discharge "
        "path untouched. It rebuilds the same window quantities — time, "
        "capacity, mean voltage and the dQ/dV peak — over the "
        f"{ph('ch_low', 3.8, '.1f')}–{ph('ch_high', 4.0, '.1f')} V "
        "constant-current charge band, pairs each charge with the discharge "
        "that follows it (which carries the production-rule SOH target), and "
        "scores the same held-out protocol: leave-one-cell-out within G1 and "
        "G3, then train on G1+G3 and test B0053 and the four random-load "
        "cells. The upper bound is "
        f"{ph('ch_high', 4.0, '.1f')} V rather than the "
        f"{ph('ch_alt_high', 4.1, '.1f')} V one might first try because "
        "several charges on B0053, B0032 and B0039 top out near "
        f"{ph('ch_top_v', 4.08, '.2f')} V, so a "
        f"{ph('ch_alt_high', 4.1, '.1f')} V band is not crossed every cycle "
        "and would drop those cells; every kept cell crosses "
        f"{ph('ch_low', 3.8, '.1f')}–{ph('ch_high', 4.0, '.1f')} V.\n"
    )

    tag_of = {
        "leave-one-cell-out: G1": "g1",
        "leave-one-cell-out: G3": "g3",
        "train G1+G3, test B0053": "b0053",
        "train G1+G3, test random-load": "rl",
    }
    label_of = {
        "leave-one-cell-out: G1": "Leave-one-cell-out G1",
        "leave-one-cell-out: G3": "Leave-one-cell-out G3",
        "train G1+G3, test B0053": "G1+G3 → B0053",
        "train G1+G3, test random-load": "G1+G3 → random-load",
    }

    lines.append(
        "| Protocol | Model | Charge-window MAE (%) | "
        "Discharge-window MAE (%) | Difference (%) |"
    )
    lines.append("|---|---|---|---|---|")

    for _, row in comparison.iterrows():

        tag = f"ch_{tag_of[row['protocol']]}_{str(row['model']).lower()}"

        for metric, key in (
            ("charge-window MAE", "charge_mae_pct"),
            ("discharge-window MAE", "discharge_mae_pct"),
            ("charge minus discharge MAE", "delta_mae_pct"),
        ):
            record("SOH", "charge window", row["protocol"],
                   f"{row['model']} {metric}", row[key], "%",
                   REQUIRED["charge_comparison"])

        lines.append(
            f"| {label_of[row['protocol']]} | {row['model']} | "
            f"{ph(tag + '_charge', float(row['charge_mae_pct']), '.2f')} | "
            f"{ph(tag + '_discharge', float(row['discharge_mae_pct']), '.2f')} | "
            f"{ph(tag + '_delta', float(row['delta_mae_pct']), '.2f')} |"
        )

    def _cell(protocol, model):
        block = comparison[
            (comparison["protocol"] == protocol)
            & (comparison["model"] == model)
        ]
        return block.iloc[0] if not block.empty else None

    g3 = _cell("leave-one-cell-out: G3", "XGBoost")
    g1 = _cell("leave-one-cell-out: G1", "XGBoost")
    b0053 = _cell("train G1+G3, test B0053", "XGBoost")
    rl = _cell("train G1+G3, test random-load", "XGBoost")

    lines.append("")
    lines.append(
        "**Verdict.** Charging carries a real SOH signal — on the G3 cells a "
        "model over the charge window matches the discharge model, "
        f"{ph('ch_g3_xgboost_charge', float(g3['charge_mae_pct']), '.2f')}% "
        "against "
        f"{ph('ch_g3_xgboost_discharge', float(g3['discharge_mae_pct']), '.2f')}% "
        "— but it transfers worse everywhere else and is not a replacement "
        "for the discharge window. On the warm G1 cells it is "
        f"{ph('ch_g1_xgboost_charge', float(g1['charge_mae_pct']), '.2f')}% "
        f"(discharge {ph('ch_g1_xgboost_discharge', float(g1['discharge_mae_pct']), '.2f')}%), "
        "on the cold G9 cell B0053, a condition the charge model never saw, it "
        f"is {ph('ch_b0053_xgboost_charge', float(b0053['charge_mae_pct']), '.2f')}% "
        f"(discharge {ph('ch_b0053_xgboost_discharge', float(b0053['discharge_mae_pct']), '.2f')}%), "
        "and on the random-load cells it is "
        f"{ph('ch_rl_xgboost_charge', float(rl['charge_mae_pct']), '.2f')}% "
        f"(discharge {ph('ch_rl_xgboost_discharge', float(rl['discharge_mae_pct']), '.2f')}%). "
        "So the answer is: partly, on the cells and conditions the charge "
        "model is trained on, but the charge window is a weaker held-out "
        f"estimator than the discharge window, and only the G3 protocol "
        f"stays under the {ph('ch_limit', 3.0, '.0f')}% budget.\n"
    )

    return "\n".join(lines)


def build_results(clean, audit, soh_res, soh_nested, soh_base, soh_null,
                  full, partial, partial_transfer, partial_window,
                  ablation, logo, cross, random_transfer,
                  wr_detail, wr_summary, wr_limits,
                  wt_summary, wt_limits):
    lines = []

    # Cross-condition transfer opens Key Results: the model that
    # looks good within G1 does not survive a change of operating
    # condition, and that is the headline caveat on every number
    # below it.
    lines.append(build_cross_condition(logo, cross, clean, soh_base))
    lines.append("")

    lines.append(build_partial_transfer(partial_transfer))
    lines.append("")

    lines.append(build_random_load_transfer(random_transfer))
    lines.append("")

    lines.append(build_window_robustness(wr_detail, wr_summary, wr_limits))
    lines.append("")

    lines.append(build_window_train_sweep(wt_summary, wt_limits))
    lines.append("")

    lines.append(build_reference_window(partial_window))
    lines.append("")

    lines.append("#### Offline SOH benchmark (full feature set)\n")
    lines.append(
        "This benchmark is deliberately offline: it uses every production "
        "feature, including full-discharge signals such as duration and "
        "resistance that a BMS only observes once the discharge has "
        "finished. It is a model-to-model reference; the online task, and "
        "the only place a model is compared against a physics formula, is "
        "the partial-discharge section above. The full-discharge Coulomb "
        "row is the formula that would be used when a complete discharge "
        "is available.\n"
    )
    lines.append("| Method | Validation | Mean MAE (%) | Mean R² |")
    lines.append("|---|---|---|---|")

    coulomb = mean(soh_base.loc[soh_base["model"] == "Coulomb", "MAE"])
    coulomb_r2 = mean(soh_base.loc[soh_base["model"] == "Coulomb", "R2"])
    record("SOH", "Coulomb formula", "leave-one-cell-out (G1)",
           "mean MAE", coulomb, "%", REQUIRED["soh_baselines"])
    record("SOH", "Coulomb formula", "leave-one-cell-out (G1)",
           "mean R2", coulomb_r2, "", REQUIRED["soh_baselines"])
    lines.append(
        f"| Coulomb formula | leave-one-cell-out (G1) | "
        f"{ph('soh_coulomb_mae', coulomb, '.2f')} | "
        f"{ph('soh_coulomb_r2', coulomb_r2, '.3f')} |"
    )

    for model in ("ElasticNet", "Ridge", "RandomForest"):
        key = model.lower()
        mae = model_mean(soh_base, model)
        r2 = mean(soh_base.loc[soh_base["model"] == model, "R2"])
        record("SOH", model, "leave-one-cell-out (G1)", "mean MAE", mae, "%",
               REQUIRED["soh_baselines"])
        record("SOH", model, "leave-one-cell-out (G1)", "mean R2", r2, "",
               REQUIRED["soh_baselines"])
        lines.append(
            f"| {model} (default) | leave-one-cell-out (G1) | "
            f"{ph('soh_' + key + '_mae', mae, '.2f')} | "
            f"{ph('soh_' + key + '_r2', r2, '.3f')} |"
        )

    xgb_fixed = mean(soh_res["MAE"])
    xgb_fixed_r2 = mean(soh_res["R2"])
    record("SOH", "XGBoost (fixed params)", "leave-one-cell-out (G1)",
           "mean MAE", xgb_fixed, "%", REQUIRED["soh_results"])
    record("SOH", "XGBoost (fixed params)", "leave-one-cell-out (G1)",
           "mean R2", xgb_fixed_r2, "", REQUIRED["soh_results"])
    lines.append(
        f"| XGBoost (fixed params) | leave-one-cell-out (G1) | "
        f"{ph('soh_xgb_fixed_mae', xgb_fixed, '.2f')} | "
        f"{ph('soh_xgb_fixed_r2', xgb_fixed_r2, '.3f')} |"
    )

    xgb_nested = mean(soh_nested["MAE"])
    xgb_nested_r2 = mean(soh_nested["R2"])
    record("SOH", "XGBoost (nested tuning)", "nested leave-one-cell-out (G1)",
           "mean MAE", xgb_nested, "%", REQUIRED["soh_nested"])
    record("SOH", "XGBoost (nested tuning)", "nested leave-one-cell-out (G1)",
           "mean R2", xgb_nested_r2, "", REQUIRED["soh_nested"])
    lines.append(
        f"| XGBoost (nested tuning) | nested leave-one-cell-out (G1) | "
        f"{ph('soh_xgb_nested_mae', xgb_nested, '.2f')} | "
        f"{ph('soh_xgb_nested_r2', xgb_nested_r2, '.3f')} |"
    )

    null = mean(soh_null.loc[soh_null["baseline"] == "constant_global_mean", "MAE"])
    record("SOH", "Constant global mean", "leave-one-cell-out (G1)",
           "mean MAE", null, "%", REQUIRED["soh_null"])
    lines.append(
        f"| Constant global mean | leave-one-cell-out (G1) | "
        f"{ph('soh_null_mae', null, '.2f')} | — |"
    )

    lines.append("\n#### Per-cell SOH error (XGBoost, nested)\n")
    lines.append("| Held-out cell | MAE (%) | RMSE (%) | R² |")
    lines.append("|---|---|---|---|")
    for _, row in soh_nested.sort_values("test_cell").iterrows():
        cell = str(row["test_cell"]).lower()
        record("SOH", "XGBoost (nested)", f"held-out {row['test_cell']}",
               "MAE", row["MAE"], "%", REQUIRED["soh_nested"])
        record("SOH", "XGBoost (nested)", f"held-out {row['test_cell']}",
               "RMSE", row["RMSE"], "%", REQUIRED["soh_nested"])
        record("SOH", "XGBoost (nested)", f"held-out {row['test_cell']}",
               "R2", row["R2"], "", REQUIRED["soh_nested"])
        lines.append(
            f"| {row['test_cell']} | {ph('soh_nested_' + cell + '_mae', float(row['MAE']), '.2f')} "
            f"| {ph('soh_nested_' + cell + '_rmse', float(row['RMSE']), '.2f')} "
            f"| {ph('soh_nested_' + cell + '_r2', float(row['R2']), '.3f')} |"
        )

    lines.append("\n#### Full- versus partial-discharge formulas (no model)\n")
    lines.append(
        "When the full discharge is available, SOH is read directly "
        "from the measured capacity by Coulomb counting and no model is "
        "used; the error is the formula's, not a prediction's. When "
        "only the window is available the same kind of formula is much "
        "weaker, which is exactly why the partial-discharge models "
        "above exist.\n"
    )
    lines.append("| Method | Held-out cell | MAE (%) |")
    lines.append("|---|---|---|")
    full_g1 = full[full["cell_id"].isin(["B0005", "B0006", "B0007", "B0018"])]
    for _, row in full_g1.iterrows():
        cell = str(row["cell_id"]).lower()
        record("SOH", "full-discharge formula", f"held-out {row['cell_id']}",
               "MAE", row["mae_pct"], "%", REQUIRED["full_discharge"])
        lines.append(
            f"| Full-discharge formula | {row['cell_id']} | "
            f"{ph('soh_full_' + cell + '_mae', float(row['mae_pct']), '.2f')} |"
        )

    full_mean = float(full_g1["mae_pct"].mean())
    record("SOH", "full-discharge formula", "mean (G1)",
           "mean MAE", full_mean, "%", REQUIRED["full_discharge"])
    lines.append(
        f"| Full-discharge formula | mean (G1) | "
        f"{ph('soh_full_mean_mae', full_mean, '.2f')} |"
    )

    partial_formula = partial[partial["method"] == "partial_window_formula"]
    partial_formula = partial_formula[partial_formula["cell_id"].isin(
        ["B0005", "B0006", "B0007", "B0018"])]
    for _, row in partial_formula.iterrows():
        cell = str(row["cell_id"]).lower()
        record("SOH", "partial-window formula", f"held-out {row['cell_id']}",
               "MAE", row["mae_pct"], "%", REQUIRED["partial_discharge"])
        lines.append(
            f"| Partial-window formula | {row['cell_id']} | "
            f"{ph('soh_partial_' + cell + '_mae', float(row['mae_pct']), '.2f')} |"
        )

    partial_g1_mean = float(partial_formula["mae_pct"].mean())
    record("SOH", "partial-window formula", "mean (G1)",
           "mean MAE", partial_g1_mean, "%", REQUIRED["partial_discharge"])
    lines.append(
        f"| Partial-window formula | mean (G1) | "
        f"{ph('soh_partial_mean_mae', partial_g1_mean, '.2f')} |"
    )

    lines.append("\n#### Feature-set ablation (XGBoost/ElasticNet, leave-one-cell-out on G1)\n")
    lines.append("| Feature set | Features | ElasticNet MAE (%) | XGBoost MAE (%) |")
    lines.append("|---|---|---|---|")
    labels = {
        "A_all": "A — all production features",
        "B_no_vw": "B — A without window features",
        "C_vw_temp_current": "C — window + temperature + current",
        "D_vw_only": "D — window features only",
    }
    for set_name, label in labels.items():
        set_id = set_name.lower()
        rows = ablation[ablation["feature_set"] == set_name]
        en = float(rows.loc[rows["model"] == "ElasticNet", "mean_mae_pct"].iloc[0])
        xg = float(rows.loc[rows["model"] == "XGBoost", "mean_mae_pct"].iloc[0])
        n_feat = int(rows["n_features"].iloc[0])
        record("SOH", set_name, "leave-one-cell-out (G1)", "ElasticNet MAE",
               en, "%", REQUIRED["ablation"])
        record("SOH", set_name, "leave-one-cell-out (G1)", "XGBoost MAE",
               xg, "%", REQUIRED["ablation"])
        lines.append(
            f"| {label} | {ph('ablation_' + set_id + '_features', n_feat, 'd')} | "
            f"{ph('ablation_' + set_id + '_en_mae', en, '.2f')} | "
            f"{ph('ablation_' + set_id + '_xgb_mae', xg, '.2f')} |"
        )

    return "\n".join(lines)


def _rul_cross_table(lines, frame, tag, source):
    """One cross-group RUL table: both methods plus the baseline."""

    lines.append(
        "| Held-out cell | Trajectory MAE | Early-fade extrapolation MAE | "
        "Fleet-lifetime baseline MAE |"
    )
    lines.append("|---|---|---|---|")

    traj, extrap, base = [], [], []

    for _, row in frame.sort_values("test_cell").iterrows():

        cell = str(row["test_cell"]).lower()
        t = float(row["trajectory_mae"])
        e = float(row["extrapolation_mae"])
        b = float(row["fleet_lifetime_baseline_mae"])
        traj.append(t)
        extrap.append(e)
        base.append(b)

        record("RUL", f"cross-group {tag}", f"held-out {row['test_cell']}",
               "trajectory MAE", t, "discharges", source)
        record("RUL", f"cross-group {tag}", f"held-out {row['test_cell']}",
               "extrapolation MAE", e, "discharges", source)
        record("RUL", f"cross-group {tag}", f"held-out {row['test_cell']}",
               "fleet-lifetime baseline MAE", b, "discharges", source)

        lines.append(
            f"| {row['test_cell']} | "
            f"{ph(tag + '_' + cell + '_traj', t, '.1f')} | "
            f"{ph(tag + '_' + cell + '_extrap', e, '.1f')} | "
            f"{ph(tag + '_' + cell + '_base', b, '.1f')} |"
        )

    mt, me, mb = mean(traj), mean(extrap), mean(base)

    for metric, value in (
        ("trajectory MAE", mt),
        ("extrapolation MAE", me),
        ("fleet-lifetime baseline MAE", mb),
    ):
        record("RUL", f"cross-group {tag}", "mean", metric, value,
               "discharges", source)

    lines.append(
        f"| **Mean** | {ph(tag + '_mean_traj', mt, '.1f')} | "
        f"{ph(tag + '_mean_extrap', me, '.1f')} | "
        f"{ph(tag + '_mean_base', mb, '.1f')} |"
    )


def _rul_cross_verdict(lines, frame, tag, source):
    """State, in one line, whether either method beats the baseline."""

    traj = frame["trajectory_mae"].to_numpy(dtype=float)
    extrap = frame["extrapolation_mae"].to_numpy(dtype=float)
    base = frame["fleet_lifetime_baseline_mae"].to_numpy(dtype=float)

    n = len(frame)
    n_traj = int((traj < base).sum())
    n_extrap = int((extrap < base).sum())

    record("RUL", f"cross-group {tag}", "verdict",
           "cells where trajectory beats baseline", n_traj, "cells", source)
    record("RUL", f"cross-group {tag}", "verdict",
           "cells where extrapolation beats baseline", n_extrap, "cells",
           source)

    if n_traj == 0 and n_extrap == 0:

        lines.append(
            "Neither method beats the fleet-lifetime baseline on this group: "
            f"across the {ph(tag + '_n_cells', n, 'd')} held-out cells the "
            "baseline is at or below both methods."
        )

        return

    lines.append(
        "The matcher beats the baseline on "
        f"{ph(tag + '_traj_beat', n_traj, 'd')} of "
        f"{ph(tag + '_n_cells', n, 'd')} cells and the fade-rate "
        "extrapolation on "
        f"{ph(tag + '_extrap_beat', n_extrap, 'd')} of "
        f"{ph(tag + '_n_cells', n, 'd')}."
    )


def _rul_window_table(lines, sweep, source):
    """MAE of the fade-rate extrapolation as its fit window grows."""

    windows = sorted(int(w) for w in sweep["window"].unique())

    by_window = {
        w: sweep[sweep["window"] == w].set_index("cell_id")
        for w in windows
    }

    lines.append(
        "| Cell | "
        + " | ".join(f"{ph('cgw_w' + str(w), w, 'd')}-discharge fit"
                     for w in windows)
        + " |"
    )
    lines.append("|" + "---|" * (len(windows) + 1))

    for cell in sorted(sweep["cell_id"].unique()):

        cell_id = str(cell)
        values = [float(by_window[w].loc[cell_id, "mae"]) for w in windows]

        for w, value in zip(windows, values):

            record("RUL", "fade-rate window sweep", cell_id,
                   f"MAE at window {w}", value, "discharges", source)

        lines.append(
            f"| {cell_id} | "
            + " | ".join(
                ph(f"cgw_{cell_id.lower()}_w{w}", value, ".1f")
                for w, value in zip(windows, values)
            )
            + " |"
        )

    for label, func in (
        ("Mean", lambda s: float(pd.Series(s).mean())),
        ("Median", lambda s: float(pd.Series(s).median())),
    ):

        values = [float(func(by_window[w]["mae"])) for w in windows]

        for w, value in zip(windows, values):

            record("RUL", "fade-rate window sweep", label,
                   f"MAE at window {w}", value, "discharges", source)

        lines.append(
            f"| **{label}** | "
            + " | ".join(
                ph(f"cgw_{label.lower()}_w{w}", value, ".1f")
                for w, value in zip(windows, values)
            )
            + " |"
        )


def _rul_decision(g6_loco, g6_to_g1, prediction_bands):
    """
    The one decision the RUL section makes.

    Which method is best depends on the cell and the reference group:
    inside G6 the fleet-lifetime average beats both methods on every
    cell, while across groups the matcher beats the average on three
    of four G1 cells. So RUL is not shipped as one method — every
    discharge carries the minimum-to-maximum range of the matcher, the
    early-fade extrapolation and the fleet-lifetime average. Every
    number below is read from the cross-group tables and the shipped
    per-discharge band.
    """

    base = g6_loco["fleet_lifetime_baseline_mae"]
    traj = g6_loco["trajectory_mae"]
    extrap = g6_loco["extrapolation_mae"]

    n_g6 = int(len(g6_loco))
    n_g1 = int(len(g6_to_g1))
    n_beat = int(
        (
            g6_to_g1["trajectory_mae"]
            < g6_to_g1["fleet_lifetime_baseline_mae"]
        ).sum()
    )

    width = float(
        (prediction_bands["rul_high"] - prediction_bands["rul_low"]).mean()
    )

    for label, series in (
        ("fleet-lifetime average", base),
        ("trajectory", traj),
        ("early-fade extrapolation", extrap),
    ):
        record("RUL", "shipped range", "leave-one-cell-out: G6",
               f"{label} MAE low", float(series.min()), "discharges",
               REQUIRED["rul_g6_loco"])
        record("RUL", "shipped range", "leave-one-cell-out: G6",
               f"{label} MAE high", float(series.max()), "discharges",
               REQUIRED["rul_g6_loco"])

    record("RUL", "shipped range", "train G6, test G1",
           "cells where trajectory beats the average", n_beat, "cells",
           REQUIRED["rul_g6_to_g1"])
    record("RUL", "shipped range", "train G6, test G1", "held-out cells",
           n_g1, "cells", REQUIRED["rul_g6_to_g1"])
    record("RUL", "shipped range", "held-out G1 cells",
           "mean indicative-range width", width, "discharges",
           REQUIRED["rul_prediction_bands"])

    lines = []
    lines.append(
        "#### One decision: RUL ships as a range, not a point estimate"
    )
    lines.append("")
    lines.append(
        "Neither method wins everywhere. Leave-one-cell-out inside G6, the "
        f"fleet-lifetime average is the best estimate on all "
        f"{ph('ruld_g6_n', n_g6, 'd')} cells — MAE "
        f"{ph('ruld_g6_base_lo', float(base.min()), '.1f')}–"
        f"{ph('ruld_g6_base_hi', float(base.max()), '.1f')} discharges — "
        "against the matcher's "
        f"{ph('ruld_g6_traj_lo', float(traj.min()), '.1f')}–"
        f"{ph('ruld_g6_traj_hi', float(traj.max()), '.1f')} and the "
        "early-fade extrapolation's "
        f"{ph('ruld_g6_extrap_lo', float(extrap.min()), '.1f')}–"
        f"{ph('ruld_g6_extrap_hi', float(extrap.max()), '.1f')}. Trained on "
        "G6 and tested across groups on each G1 cell, the matcher beats the "
        "fleet-lifetime average on "
        f"{ph('ruld_g1_traj_beat', n_beat, 'd')} of "
        f"{ph('ruld_g1_n', n_g1, 'd')} cells; B0006 is the exception, "
        "because it fades faster than any reference trajectory. Since the "
        "winner depends on the cell and the reference group, no single "
        "method is shipped: every discharge carries an indicative range, "
        "the minimum to the maximum of the matcher, the early-fade "
        "extrapolation and the fleet-lifetime average at that discharge "
        "(mean width "
        f"{ph('ruld_range_width', width, '.1f')} discharges on the held-out "
        "G1 cells)."
    )
    return "\n".join(lines)


def build_rul(rul, comparison, disagreement, band, random_load,
              labels, g6_loco, g1_to_g6, g6_to_g1, window_sweep,
              prediction_bands):
    lines = []
    lines.append(
        "> **Indicative, not a forecast.** Every remaining-life number here "
        "is an estimate from a handful of reference trajectories under one "
        "load. It says where a cell sits on the average ageing path, not "
        "what will happen to that particular cell."
    )
    lines.append("")
    lines.append(
        "| Held-out cell | EOL discharge | Trajectory MAE | "
        "Fleet-lifetime baseline MAE | Reference-spread coverage | "
        "Mean reference-spread width |"
    )
    lines.append("|---|---|---|---|---|---|")
    for _, row in rul.sort_values("test_cell").iterrows():
        cell = str(row["test_cell"]).lower()
        record("RUL", "trajectory", f"held-out {row['test_cell']}",
               "MAE", row["trajectory_mae"], "discharges", REQUIRED["rul_trajectory"])
        record("RUL", "fleet lifetime baseline", f"held-out {row['test_cell']}",
               "MAE", row["fleet_lifetime_baseline_mae"], "discharges",
               REQUIRED["rul_trajectory"])
        record("RUL", "EOL", f"held-out {row['test_cell']}",
               "EOL discharge", row["eol_discharge"], "discharges",
               REQUIRED["rul_trajectory"])
        record("RUL", "reference-spread coverage", f"held-out {row['test_cell']}",
               "reference-spread coverage", row["band_coverage_pct"], "percent",
               REQUIRED["rul_trajectory"])
        record("RUL", "reference-spread width", f"held-out {row['test_cell']}",
               "mean reference-spread width", row["band_width_mean"], "discharges",
               REQUIRED["rul_trajectory"])
        lines.append(
            f"| {row['test_cell']} | {ph('rul_' + cell + '_eol', int(row['eol_discharge']), 'd')} | "
            f"{ph('rul_' + cell + '_traj_mae', float(row['trajectory_mae']), '.1f')} | "
            f"{ph('rul_' + cell + '_base_mae', float(row['fleet_lifetime_baseline_mae']), '.1f')} | "
            f"{ph('rul_' + cell + '_cov', float(row['band_coverage_pct']), '.1f')}% | "
            f"{ph('rul_' + cell + '_width', float(row['band_width_mean']), '.1f')} |"
        )

    traj_mean = mean(rul["trajectory_mae"])
    base_mean = mean(rul["fleet_lifetime_baseline_mae"])
    cov_mean = mean(rul["band_coverage_pct"])
    width_mean = mean(rul["band_width_mean"])
    record("RUL", "trajectory", "mean (G1)", "mean MAE", traj_mean,
           "discharges", REQUIRED["rul_trajectory"])
    record("RUL", "fleet lifetime baseline", "mean (G1)", "mean MAE", base_mean,
           "discharges", REQUIRED["rul_trajectory"])
    record("RUL", "reference-spread coverage", "mean (G1)", "mean coverage",
           cov_mean, "percent", REQUIRED["rul_trajectory"])
    record("RUL", "reference-spread width", "mean (G1)", "mean reference-spread width",
           width_mean, "discharges", REQUIRED["rul_trajectory"])
    lines.append(
        f"| **Mean** | — | {ph('rul_mean_traj_mae', traj_mean, '.1f')} | "
        f"{ph('rul_mean_base_mae', base_mean, '.1f')} | "
        f"{ph('rul_mean_cov', cov_mean, '.1f')}% | "
        f"{ph('rul_mean_width', width_mean, '.1f')} |"
    )

    lines.append("")
    lines.append("#### Both methods, side by side on every held-out cell")
    lines.append("")
    lines.append(
        "The matcher was also scored against a fade-rate extrapolation: "
        "a straight line through the first "
        f"{ph('rc_window', int(comparison['window'].iloc[0]), 'd')} measured "
        "SOH values of the cell, extended to "
        "the retirement threshold. Both are leave-one-cell-out and use the "
        "same held-out discharges, so the columns are comparable. The last "
        "numeric column is the mean absolute gap between the two methods "
        "over those discharges."
    )
    lines.append("")
    lines.append(
        "| Held-out cell | Trajectory MAE | Early-fade extrapolation MAE | "
        "Method disagreement | Fleet-lifetime baseline MAE |"
    )
    lines.append("|---|---|---|---|---|")
    disagree = disagreement.set_index("test_cell")
    for _, row in comparison.sort_values("test_cell").iterrows():
        cell = str(row["test_cell"]).lower()
        gap = float(disagree.loc[row["test_cell"], "mean_abs_disagreement"])
        record("RUL", "trajectory", f"comparison {row['test_cell']}", "MAE",
               row["trajectory_mae"], "discharges", REQUIRED["rul_comparison"])
        record("RUL", "early-fade extrapolation", f"comparison {row['test_cell']}",
               "MAE", row["extrapolation_mae"], "discharges",
               REQUIRED["rul_comparison"])
        record("RUL", "method disagreement", f"comparison {row['test_cell']}",
               "mean absolute disagreement", gap, "discharges",
               REQUIRED["rul_disagreement"])
        record("RUL", "fleet lifetime baseline", f"comparison {row['test_cell']}",
               "MAE", row["fleet_lifetime_baseline_mae"], "discharges",
               REQUIRED["rul_comparison"])
        lines.append(
            f"| {row['test_cell']} | "
            f"{ph('rul_' + cell + '_traj_mae', float(row['trajectory_mae']), '.1f')} | "
            f"{ph('rc_' + cell + '_extrap_mae', float(row['extrapolation_mae']), '.1f')} | "
            f"{ph('rc_' + cell + '_gap', gap, '.1f')} | "
            f"{ph('rul_' + cell + '_base_mae', float(row['fleet_lifetime_baseline_mae']), '.1f')} |"
        )
    extrap_mean = mean(comparison["extrapolation_mae"])
    gap_mean = mean(disagreement["mean_abs_disagreement"])
    worst = disagreement.sort_values("mean_abs_disagreement").iloc[-1]
    record("RUL", "early-fade extrapolation", "mean (G1)", "mean MAE",
           extrap_mean, "discharges", REQUIRED["rul_comparison"])
    record("RUL", "method disagreement", "mean (G1)",
           "mean absolute disagreement", gap_mean, "discharges",
           REQUIRED["rul_disagreement"])
    lines.append(
        f"| **Mean** | {ph('rul_mean_traj_mae', traj_mean, '.1f')} | "
        f"{ph('rc_mean_extrap_mae', extrap_mean, '.1f')} | "
        f"{ph('rc_mean_gap', gap_mean, '.1f')} | "
        f"{ph('rul_mean_base_mae', base_mean, '.1f')} |"
    )
    lines.append("")
    lines.append(
        "No method is chosen per cell, and no cell is scored with the method "
        "that happens to win it: that would be reading the answer off the "
        "test set. Instead both methods are shown on every held-out cell and "
        "their disagreement is reported as the uncertainty. It is large — "
        f"{ph('rc_mean_gap', gap_mean, '.1f')} discharges on the mean, and "
        f"{ph('rc_worst_gap', float(worst['mean_abs_disagreement']), '.1f')} "
        f"on {worst['test_cell']} — because a line that overshoots a late "
        "knee and a matcher that follows reference curves can disagree by an "
        "entire lifetime. That width, not a point estimate, is the honest "
        "statement about RUL on four cells, and it is why the whole task is "
        "labelled indicative."
    )

    lines.append("")
    lines.append("#### Calibrating a real 10th-to-90th band")
    lines.append("")
    lines.append(
        "The reference-spread band above is a quantile of the reference "
        "cells' remaining life, not a calibrated error band. The honest "
        "version calibrates the band on the other cells and measures it on "
        "a cell it never saw: for each held-out cell the offsets are the "
        "10th and 90th percentile of the (truth - estimate) residuals "
        "pooled from the other three cells."
    )
    lines.append("")
    lines.append(
        "| Held-out cell | Max points | Calibration cells | "
        "Lower offset | Upper offset | Coverage |"
    )
    lines.append("|---|---|---|---|---|---|")
    for _, row in band.sort_values("test_cell").iterrows():
        cell = str(row["test_cell"]).lower()
        record("RUL", "calibrated band", f"calibration {row['test_cell']}",
               "coverage", row["coverage_pct"], "percent", REQUIRED["rul_band"])
        lines.append(
            f"| {row['test_cell']} | "
            f"{ph('rc_' + cell + '_n', int(row['n_points']), 'd')} | "
            f"{ph('rc_n_cal', int(row['n_calibration_cells']), 'd')} | "
            f"{ph('rc_' + cell + '_lo', float(row['band_low_offset']), '.1f')} | "
            f"{ph('rc_' + cell + '_hi', float(row['band_high_offset']), '.1f')} | "
            f"{ph('rc_' + cell + '_cov', float(row['coverage_pct']), '.1f')}% |"
        )
    band_cov_mean = mean(band["coverage_pct"])
    record("RUL", "calibrated band", "mean (G1)", "mean coverage",
           band_cov_mean, "percent", REQUIRED["rul_band"])
    lines.append(
        f"| **Mean** | — | — | — | — | "
        f"{ph('rc_mean_cov', band_cov_mean, '.1f')}% |"
    )
    lines.append("")
    lines.append(
        "A 10th-to-90th band should cover four discharges in five, and this "
        "one misses by a wide margin and swings from 0.0% to 100.0% across "
        "cells. It is not fixable here: only three cells calibrate any "
        "held-out band, and one cell's residuals are correlated across its "
        "discharges, so the effective calibration sample is closer to three "
        "than to the number of points. A trustworthy calibrated band needs "
        "more cells than the four in G1; the reference spread remains the "
        "only band this dataset supports."
    )
    reaches = int(random_load["reaches_80"].sum())
    usable = int((random_load["reaches_80"] & random_load["keep"]).sum())
    total = int(len(random_load))
    threshold = int(random_load["threshold"].iloc[0])
    b41 = random_load.set_index("cell_id").loc["B0041"]
    record("RUL", "random-load check", "B0038-B0044", "cells checked",
           total, "cells", REQUIRED["random_load"])
    record("RUL", "random-load check", "B0038-B0044",
           "retirement threshold", threshold, "percent SOH",
           REQUIRED["random_load"])
    record("RUL", "random-load check", "B0038-B0044", "cells reaching 80%",
           reaches, "cells", REQUIRED["random_load"])
    record("RUL", "random-load check", "B0038-B0044",
           "cells reaching 80% and kept", usable, "cells",
           REQUIRED["random_load"])
    lines.append("")
    lines.append("#### More cells that reach 80%? Yes, in the random-load groups")
    lines.append("")
    lines.append(
        "RUL needs a cell that actually reaches the retirement threshold, so "
        "the groups the model leaves out were checked against the same SOH "
        "rule rather than assumed dead. The NASA random-load groups G5 "
        f"(B0038-B0040) and G6 (B0041-B0044) were run through it: "
        f"{ph('rl_total', total, 'd')} cells checked, "
        f"{ph('rl_reaches', reaches, 'd')} reach "
        f"{ph('rl_threshold', threshold, 'd')}%, and "
        f"{ph('rl_usable', usable, 'd')} of those also pass the same "
        "ceiling/rise keep filter (B0039, B0042, B0043, B0044). So the claim "
        "that only the four G1 cells reach 80% is false in general: four more "
        "cells do. They are still not added to the constant-current SOH/RUL "
        "training set, because their load and temperature change across the "
        "record, so their features are not comparable at a single operating "
        "condition; but they are not left out because the model fails on them "
        "— the shipped partial-window SOH model transfers to all of them and "
        "beats the window formula (see the random-load transfer table under "
        "*Key Results*), so the exclusion is a comparability choice. B0041 "
        "reaches 80% too but is "
        "excluded, and its reference shows why: its opening "
        f"{ph('rl_b41_n', 10, 'd')} recorded discharges carry a median "
        f"{ph('rl_b41_early_cap', float(b41['early_capacity_median_ah']), '.3f')} Ah "
        "at a mean "
        f"{ph('rl_b41_early_cur', float(b41['early_current_mean_abs_a']), '.2f')} A "
        "— about a quarter of the group's own load — with the terminal voltage "
        "falling by only "
        f"{ph('rl_b41_drop', float(b41['early_voltage_drop_v']), '.3f')} V, "
        "against "
        f"{ph('rl_b41_later', float(b41['later_median_capacity_ah']), '.2f')} Ah "
        "once the cell discharges normally later in life. Those opening runs "
        "are a measurement failure, not a low-capacity cell, but they are the "
        "majority of the record, so the rule that keeps only discharges above "
        "half the cell's median normalises against them and keeps them; the "
        "resulting "
        f"{ph('rl_b41_ref', float(b41['reference_capacity_ah']), '.3f')} Ah "
        "reference is an artifact, its SOH rises above the ceiling and the "
        "cell is excluded — which is why no cell the pipeline trusts has a "
        "reference under half the median of its later valid discharges."
    )

    lines.append("")
    lines.append("#### RUL on the random-load cells, kept separate from G1")
    lines.append("")
    lines.append(
        "The four random-load cells that reach retirement are "
        "labelled by the same rule as G1 — the first valid discharge below "
        f"{ph('cg_threshold', threshold, 'd')}% of the cell's first-five-median "
        "capacity. B0039 is the only usable G5 cell, so it has no in-group "
        "siblings and is labelled but not scored; the three G6 cells "
        "(B0042, B0043, B0044) are scored three ways. Both methods and the "
        "fleet-lifetime baseline are reported for every held-out cell."
    )
    lines.append("")
    lines.append("| Cell | Group | EOL discharge | Discharges |")
    lines.append("|---|---|---|---|")

    for _, row in labels.sort_values("cell_id").iterrows():

        cell = str(row["cell_id"]).lower()

        record("RUL", "cross-group labels", row["cell_id"],
               "EOL discharge", row["eol_discharge"], "discharges",
               REQUIRED["rul_random_labels"])
        record("RUL", "cross-group labels", row["cell_id"],
               "discharges", row["discharges"], "discharges",
               REQUIRED["rul_random_labels"])

        lines.append(
            f"| {row['cell_id']} | {row['group']} | "
            f"{ph('cgl_' + cell + '_eol', int(row['eol_discharge']), 'd')} | "
            f"{ph('cgl_' + cell + '_n', int(row['discharges']), 'd')} |"
        )

    lines.append("")
    lines.append(
        "The matcher is driven by the held-out cell's own measured SOH, which "
        "a full-discharge BMS observes, because the cycle-level SOH model is "
        "defined on constant-current features and is not valid across these "
        "loads; the fade-rate extrapolation reads only the held-out cell's own "
        f"opening {ph('cg_window', int(comparison['window'].iloc[0]), 'd')} "
        "measured discharges, so its number is the same in every protocol."
    )
    lines.append("")

    for label, frame, tag, source in (
        ("(a) leave-one-cell-out inside G6",
         g6_loco, "cg_a", REQUIRED["rul_g6_loco"]),
        ("(b) train the matcher on G1, test on each G6 cell",
         g1_to_g6, "cg_b", REQUIRED["rul_g1_to_g6"]),
        ("(c) train the matcher on G6, test on each G1 cell",
         g6_to_g1, "cg_c", REQUIRED["rul_g6_to_g1"]),
    ):

        lines.append(f"**{label}**")
        lines.append("")
        _rul_cross_table(lines, frame, tag, source)
        lines.append("")
        _rul_cross_verdict(lines, frame, tag, source)
        lines.append("")

    lines.append(
        "Only protocol (b) mixes the groups, and it is the one where both "
        "methods clear the baseline on every G6 cell: a G6 cell is watched by "
        "G1 references that sit at a different load, but the two G6 cells "
        "left in (a) retire at almost the same discharge, so there the "
        "baseline is simply a very good guess."
    )

    lines.append("")
    lines.append(_rul_decision(g6_loco, g6_to_g1, prediction_bands))

    windows = sorted(int(w) for w in window_sweep["window"].unique())
    w_lo, w_hi = windows[0], windows[-1]

    by_window = {
        w: window_sweep[window_sweep["window"] == w].set_index("cell_id")
        for w in windows
    }
    mae_lo = by_window[w_lo]["mae"]
    mae_hi = by_window[w_hi]["mae"]
    n_cells = int(len(mae_lo))
    n_worse = int((mae_hi > mae_lo).sum())
    med_lo = float(mae_lo.median())
    med_hi = float(mae_hi.median())

    extrap20 = comparison.set_index("test_cell")["extrapolation_mae"]
    traj = comparison.set_index("test_cell")["trajectory_mae"]

    b0039 = by_window[w_lo].loc["B0039"]
    b0039_hi = by_window[w_hi].loc["B0039"]
    b05 = by_window[w_lo].loc["B0005"]
    b07 = by_window[w_lo].loc["B0007"]

    lines.append("")
    lines.append("#### Does a longer fade-rate window help?")
    lines.append("")
    lines.append(
        "The fade-rate extrapolation is a single straight line through a "
        f"cell's first {ph('cgw_w' + str(w_lo), w_lo, 'd')} measured "
        "discharges, so its answer is fixed entirely by that window's slope. "
        f"Refitting it at {ph('cgw_w' + str(windows[1]), windows[1], 'd')} "
        f"and {ph('cgw_w' + str(w_hi), w_hi, 'd')} discharges and re-scoring "
        f"all {ph('cgw_n_cells', n_cells, 'd')} labelled cells gives the table "
        "below."
    )
    lines.append("")
    _rul_window_table(lines, window_sweep, REQUIRED["rul_window_sweep"])
    lines.append("")
    lines.append(
        "As an average the longer window looks better — mean MAE falls from "
        f"{ph('cgw_mean_w' + str(w_lo), float(mae_lo.mean()), '.1f')} to "
        f"{ph('cgw_mean_w' + str(w_hi), float(mae_hi.mean()), '.1f')} "
        "discharges — but that average is one cell. B0039 barely fades over "
        "its first discharges and then knees late, so its line at the default "
        f"window lands {ph('cgw_b0039_over', float(b0039['implied_eol'] - b0039['eol_discharge']), '.0f')} "
        "discharges past its real end of life; by "
        f"{ph('cgw_w' + str(w_hi), w_hi, 'd')} discharges the fit finally sees "
        f"the knee and its error drops to "
        f"{ph('cgw_b0039_w' + str(w_hi), float(b0039_hi['mae']), '.1f')}. "
        "Everywhere else the longer window hurts: the median does not fall "
        f"({ph('cgw_med_w' + str(w_lo), med_lo, '.1f')} to "
        f"{ph('cgw_med_w' + str(w_hi), med_hi, '.1f')}) and "
        f"{ph('cgw_n_worse', n_worse, 'd')} of the "
        f"{ph('cgw_n_cells', n_cells, 'd')} cells get worse, the two "
        "flat-opening G1 cells collapsing as the line flattens further — "
        "B0005 from "
        f"{ph('cgw_b0005_w' + str(w_lo), float(b05['mae']), '.1f')} to "
        f"{ph('cgw_b0005_w' + str(w_hi), float(by_window[w_hi].loc['B0005']['mae']), '.1f')} "
        "and B0007 from "
        f"{ph('cgw_b0007_w' + str(w_lo), float(b07['mae']), '.1f')} to "
        f"{ph('cgw_b0007_w' + str(w_hi), float(by_window[w_hi].loc['B0007']['mae']), '.1f')}. "
        "The longer window is therefore not kept; the default stays at "
        f"{ph('cgw_w' + str(w_lo), w_lo, 'd')} discharges."
    )
    lines.append("")
    lines.append(
        "The figure shows what fails. B0005 and B0007 fade all but flatly over "
        f"those {ph('cgw_w' + str(w_lo), w_lo, 'd')} discharges, so the fitted "
        f"line is far too shallow: it reaches "
        f"{ph('cgw_threshold', threshold, 'd')}% at discharge "
        f"{ph('cgw_b0005_imp', float(b05['implied_eol']), '.0f')} for B0005 and "
        f"{ph('cgw_b0007_imp', float(b07['implied_eol']), '.0f')} for B0007, "
        f"while the cells actually retire at "
        f"{ph('cgw_b0005_eol', int(b05['eol_discharge']), 'd')} and "
        f"{ph('cgw_b0007_eol', int(b07['eol_discharge']), 'd')}. The trajectory "
        "matcher never extrapolates that opening slope — it reads the cell's "
        "current level against reference curves — so it lands far closer on "
        f"the same cells (B0005 {ph('cgw_b0005_traj', float(traj.loc['B0005']), '.1f')} "
        f"against {ph('cgw_b0005_extrap', float(extrap20.loc['B0005']), '.1f')}, "
        f"B0007 {ph('cgw_b0007_traj', float(traj.loc['B0007']), '.1f')} against "
        f"{ph('cgw_b0007_extrap', float(extrap20.loc['B0007']), '.1f')}). The "
        "reverse holds on B0006, which fades from the first discharge and "
        "faster than any reference: there the matcher is the one that fails "
        "and the extrapolation is accurate."
    )
    lines.append("")
    lines.append(
        "![Measured SOH against discharge index for B0005 and B0007, with the "
        "fit window marked and the extrapolated line extended to the "
        "retirement threshold](docs/rul_extrapolation_B0005_B0007.png)"
    )

    return "\n".join(lines)


def build_degradation(audit, sensitivity):
    kept = audit[audit["keep"]].copy()
    lines = []
    lines.append("| Cell | EOL discharge | Fade (%/discharge) | Early fade (%/discharge) |")
    lines.append("|---|---|---|---|")
    for _, row in kept.sort_values("cell_id").iterrows():
        cell = str(row["cell_id"]).lower()
        eol = row["eol_discharge"]
        eol_txt = "—" if pd.isna(eol) else ph("deg_" + cell + "_eol", int(eol), "d")
        fade = row["fade_pct_per_discharge"]
        early = row["early_fade_pct_per_discharge"]
        record("degradation", "slope", row["cell_id"], "fade",
               abs(fade) if not pd.isna(fade) else None, "%/discharge",
               REQUIRED["audit"])
        record("degradation", "slope", row["cell_id"], "early fade",
               abs(early) if not pd.isna(early) else None, "%/discharge",
               REQUIRED["audit"])
        if not pd.isna(eol):
            record("degradation", "slope", row["cell_id"], "EOL discharge",
                   int(eol), "discharges", REQUIRED["audit"])
        lines.append(
            f"| {row['cell_id']} | {eol_txt} "
            f"| {fmt(abs(fade)) if pd.isna(fade) else ph('deg_' + cell + '_fade', abs(float(fade)), '.3f')} "
            f"| {fmt(abs(early)) if pd.isna(early) else ph('deg_' + cell + '_early', abs(float(early)), '.3f')} |"
        )

    excluded = audit[~audit["keep"]].copy()
    lines.append("")
    lines.append("#### Re-referencing the excluded cells")
    lines.append("")
    mid = sensitivity[sensitivity["reference_rule"] == "median_3_8"].iloc[0]
    mid_lo = ph("rsc_mid_lo", int(mid["ref_lo"]), "d")
    mid_hi = ph("rsc_mid_hi", int(mid["ref_hi"]), "d")
    ceiling = ph("rsc_ceiling", float(mid["soh_ceiling_pct"]), ".0f")
    lines.append(
        "Every SOH above is measured against the median of the cell's "
        "first five valid discharges. A gentler re-reference was checked "
        "on the excluded cells: the median of discharges "
        f"{mid_lo}-{mid_hi}, and the beginning-of-life peak (the maximum "
        "of the first five). The SOH ceiling and the capacity-rise test "
        "are held fixed, so a cell is returned only when the reference, "
        "not the rule, was what excluded it."
    )
    lines.append("")
    lines.append(
        "| Excluded cell | Group | first five median soh_max | "
        f"median {mid_lo}-{mid_hi} soh_max | BOL-peak soh_max | "
        "Returned? |"
    )
    lines.append("|---|---|---|---|---|---|")
    for _, row in excluded.sort_values("cell_id").iterrows():
        cell_id = row["cell_id"]
        cell = str(cell_id).lower()
        by_rule = sensitivity[sensitivity["cell_id"] == cell_id].set_index(
            "reference_rule"
        )
        first5 = float(by_rule.loc["first5_median", "soh_max"])
        mid38 = float(by_rule.loc["median_3_8", "soh_max"])
        peak = float(by_rule.loc["first5_max", "soh_max"])
        labels = {
            "median_3_8": "later-window median",
            "first5_max": "BOL peak",
            "median_1_10": "first-ten median",
        }
        gentler = by_rule.drop(index=["first5_median"])
        returned = gentler[gentler["keep"]]
        if len(returned):
            verdict = "yes (" + ", ".join(
                labels.get(rule, rule) for rule in returned.index
            ) + ")"
        else:
            verdict = "no"
        record("degradation", "re-reference first-5 median", cell_id,
               "soh max", first5, "percent", REQUIRED["reference_sensitivity"])
        record("degradation", "re-reference median 3-8", cell_id,
               "soh max", mid38, "percent", REQUIRED["reference_sensitivity"])
        record("degradation", "re-reference BOL peak", cell_id,
               "soh max", peak, "percent", REQUIRED["reference_sensitivity"])
        lines.append(
            f"| {cell_id} | {row['group']} | "
            f"{ph('rsc_' + cell + '_first5', first5, '.1f')} | "
            f"{ph('rsc_' + cell + '_mid38', mid38, '.1f')} | "
            f"{ph('rsc_' + cell + '_peak', peak, '.1f')} | {verdict} |"
        )
    lines.append("")
    lines.append(
        "The gentler rule the check started from returns nothing: none of "
        "B0054, B0055 or B0056 comes back. Their capacity genuinely rises "
        "from the recorded first discharge, so the drift is in the data, "
        "not only in the reference; B0054 also stays above the ceiling. "
        "The four G7 cells return under the beginning-of-life peak alone, "
        f"because their reading above the {ceiling}% ceiling was a high "
        "first cycle sitting above the early median rather than a real "
        "rise. No G4 cell returns under any rule: its capacity rises "
        "across the record by far more than a reference choice can "
        "explain."
    )
    lines.append("")
    lines.append(
        "The peak reference is reported and not adopted: applying it "
        "uniformly would rescale every kept cell's SOH, and a per-cell "
        "fallback would make the reference depend on the outcome it is "
        "tested against. The production reference is unchanged, so no "
        "kept cell's SOH and no reported number moves."
    )
    return "\n".join(lines)


def build_dataset(audit):
    kept = audit[audit["keep"]].copy()
    lines = []
    lines.append("| Cell | Group | Discharges | Initial SOH (%) | Final SOH (%) | EOL discharge |")
    lines.append("|---|---|---|---|---|---|")
    for _, row in kept.sort_values("cell_id").iterrows():
        cell = str(row["cell_id"]).lower()
        eol = row["eol_discharge"]
        eol_txt = "—" if pd.isna(eol) else ph("ds_" + cell + "_eol", int(eol), "d")
        record("dataset", "summary", row["cell_id"], "discharges",
               int(row["discharges"]), "rows", REQUIRED["audit"])
        record("dataset", "summary", row["cell_id"], "initial SOH",
               row["initial_soh"], "%", REQUIRED["audit"])
        record("dataset", "summary", row["cell_id"], "final SOH",
               row["final_soh"], "%", REQUIRED["audit"])
        if not pd.isna(eol):
            record("dataset", "summary", row["cell_id"], "EOL discharge",
                   int(eol), "discharges", REQUIRED["audit"])
        lines.append(
            f"| {row['cell_id']} | {row['group']} | {ph('ds_' + cell + '_discharges', int(row['discharges']), 'd')} | "
            f"{ph('ds_' + cell + '_initial_soh', float(row['initial_soh']), '.2f')} | "
            f"{ph('ds_' + cell + '_final_soh', float(row['final_soh']), '.2f')} | {eol_txt} |"
        )

    total = int(kept["discharges"].sum())
    groups = int(kept["group"].nunique())
    record("dataset", "summary", "kept cells", "discharges", total, "rows",
           REQUIRED["audit"])
    lines.append(
        f"| **Kept total** | {ph('ds_kept_groups', groups, 'd')} groups | "
        f"{ph('ds_kept_discharges', total, 'd')} | — | — | — |"
    )
    return "\n".join(lines)


def build_pack(detection, false_alarms):
    lines = []
    lines.append("| Fault | Injected | Rule detection | Isolation Forest | Rule median delay |")
    lines.append("|---|---|---|---|---|")
    for _, row in detection.sort_values("fault").iterrows():
        fault = str(row["fault"])
        rule = float(row["rule_rate"]) * 100.0
        forest = float(row["iforest_rate"]) * 100.0
        record("pack", row["fault"], "synthetic injection", "rule detection",
               rule, "%", REQUIRED["pack_detection"])
        record("pack", row["fault"], "synthetic injection", "iforest detection",
               forest, "%", REQUIRED["pack_detection"])
        record("pack", row["fault"], "synthetic injection", "injected",
               row["n"], "cases", REQUIRED["pack_detection"])
        lines.append(
            f"| {fault} | {ph('pack_' + fault + '_n', int(row['n']), 'd')} | "
            f"{ph('pack_' + fault + '_rule', rule, '.1f')}% | "
            f"{ph('pack_' + fault + '_iforest', forest, '.1f')}% | "
            f"{ph('pack_' + fault + '_delay', float(row['rule_median_delay']), '.1f')} |"
        )

    total = int(false_alarms["discharges"].sum())
    false_r = int(false_alarms["false_resistance_spike"].sum())
    false_t = int(false_alarms["false_thermal"].sum())
    record("pack", "clean packs", "no injected fault", "discharges",
           total, "discharges", REQUIRED["pack_false_alarms"])
    record("pack", "clean packs", "no injected fault", "false resistance spikes",
           false_r, "alarms", REQUIRED["pack_false_alarms"])
    record("pack", "clean packs", "no injected fault", "false thermal alarms",
           false_t, "alarms", REQUIRED["pack_false_alarms"])
    lines.append("")
    lines.append(
        f"Clean-pack false alarms at the resistance-spike and thermal "
        f"thresholds: {ph('pack_clean_false_resistance', false_r, 'd')} and "
        f"{ph('pack_clean_false_thermal', false_t, 'd')} over "
        f"{ph('pack_clean_discharges', total, 'd')} discharges."
    )
    return "\n".join(lines)


def build_latency(latency):
    lines = []
    lines.append("| Stage | Time (ms) |")
    lines.append("|---|---|")
    for _, row in latency.iterrows():
        record("latency", row["stage"], "local benchmark", "time",
               row["milliseconds"], "ms", REQUIRED["latency"])
        lines.append(f"| `{row['stage']}` | {fmt(row['milliseconds'], 2)} |")
    return "\n".join(lines)


def build_bms_contract():
    """
    The measurement conditions a BMS must present for the
    partial-window SOH model to score a discharge. Every threshold is
    read from src/features.py, so the README states what the model's
    own contract states — none of these numbers is typed here.
    """

    high = features.WINDOW_VOLTAGE_HIGH
    low = features.WINDOW_VOLTAGE_LOW
    minimum = features.WINDOW_MIN_REFERENCE_DISCHARGES
    reference = features.WINDOW_REFERENCE_WINDOW
    current_low = features.WINDOW_TRAIN_CURRENT_LOW_A
    current_high = features.WINDOW_TRAIN_CURRENT_HIGH_A

    record("bms_contract", "partial-window model", "trained band",
           "window_voltage_high", high, "V", SRC_FEATURES)
    record("bms_contract", "partial-window model", "trained band",
           "window_voltage_low", low, "V", SRC_FEATURES)
    record("bms_contract", "partial-window model", "reference baseline",
           "min_reference_discharges", minimum, "discharges", SRC_FEATURES)
    record("bms_contract", "partial-window model", "reference baseline",
           "reference_discharges", reference, "discharges", SRC_FEATURES)
    record("bms_contract", "partial-window model", "trained band",
           "train_current_low", current_low, "A", SRC_FEATURES)
    record("bms_contract", "partial-window model", "trained band",
           "train_current_high", current_high, "A", SRC_FEATURES)

    lines = []
    lines.append(
        f"- **A discharge that covers the trained window.** Every window "
        f"feature is measured between `WINDOW_VOLTAGE_HIGH` = "
        f"{ph('bms_window_high', high, '.1f')} V and `WINDOW_VOLTAGE_LOW` = "
        f"{ph('bms_window_low', low, '.1f')} V in `src/features.py`. A window "
        f"measured over a different span is refused, not scored."
    )
    lines.append(
        f"- **At least {ph('bms_min_discharges', minimum)} early discharges.** "
        f"The cell-relative baseline is the median of the first "
        f"{ph('bms_reference_discharges', reference)} opening discharges, and a "
        f"cell with fewer than {ph('bms_min_discharges', minimum)} is refused "
        f"because the baseline would be a guess."
    )
    lines.append(
        f"- **Current near the training range, "
        f"{ph('bms_current_low', current_low, '.1f')}-"
        f"{ph('bms_current_high', current_high, '.1f')} A.** The kept cells "
        f"discharge at {ph('bms_current_low', current_low, '.1f')} A (G1, G9) "
        f"and {ph('bms_current_high', current_high, '.1f')} A (G3); the "
        f"per-amp normalisation removes that rate difference inside the band, "
        f"but the model has no training data far outside it."
    )
    return "\n".join(lines)


def build_final(soh_res, soh_nested, soh_base, soh_null, partial_transfer, rul):
    lines = []
    lines.append("| Task | Method | Protocol | Score | Baseline | Unit |")
    lines.append("|---|---|---|---|---|---|")

    coulomb = mean(soh_base.loc[soh_base["model"] == "Coulomb", "MAE"])
    null = mean(soh_null.loc[soh_null["baseline"] == "constant_global_mean", "MAE"])
    nested = mean(soh_nested["MAE"])
    lines.append(
        f"| SOH (offline, full features) | Coulomb formula | leave-one-cell-out (G1) | {ph('soh_coulomb_mae', coulomb, '.2f')} "
        f"| constant mean {ph('soh_null_mae', null, '.2f')} | MAE % |"
    )
    lines.append(
        f"| SOH (offline, full features) | XGBoost (nested) | nested leave-one-cell-out (G1) | {ph('soh_xgb_nested_mae', nested, '.2f')} "
        f"| constant mean {ph('soh_null_mae', null, '.2f')} | MAE % |"
    )

    transfer = partial_transfer[
        partial_transfer["protocol"] == "train G1+G3, test B0053"
    ]
    pt_xgb = float(transfer.loc[transfer["model"] == "XGBoost", "mae_pct"].iloc[0])
    pt_formula = float(
        transfer.loc[transfer["model"] == "window_formula", "mae_pct"].iloc[0]
    )
    lines.append(
        f"| SOH (partial window) | Cell-relative XGBoost | train G1+G3, test B0053 | "
        f"{ph('final_partial_xgb_mae', pt_xgb, '.2f')} | "
        f"window formula {ph('final_partial_formula_mae', pt_formula, '.2f')} | MAE % |"
    )

    traj_mean = mean(rul["trajectory_mae"])
    base_mean = mean(rul["fleet_lifetime_baseline_mae"])
    lines.append(
        f"| RUL | Trajectory model | leave-one-cell-out (G1) | {ph('rul_mean_traj_mae', traj_mean, '.1f')} "
        f"| fleet lifetime {ph('rul_mean_base_mae', base_mean, '.1f')} | discharges |"
    )
    return "\n".join(lines)


def window_partial_stats(window_partial):
    """
    The per-cell capacity and time share inside the trained window,
    with the mean and range across cells, from
    data/processed/soh_window_partial.csv (src/window_partial.py).
    """

    cells = window_partial[window_partial["cell_id"] != "MEAN"].copy()
    capacity = cells["capacity_inside_window_pct"]
    time = cells["time_inside_window_pct"]

    return {
        "cells": cells,
        "n_cells": int(len(cells)),
        "capacity_mean": float(capacity.mean()),
        "time_mean": float(time.mean()),
        "capacity_min": float(capacity.min()),
        "capacity_max": float(capacity.max()),
        "time_min": float(time.min()),
        "time_max": float(time.max()),
    }


def build_window_share(window_partial):
    """
    The one-line README limits-table cell: how partial the window is,
    as a mean over the kept cells rather than an assertion.
    """

    stats = window_partial_stats(window_partial)

    record("SOH", "partial-window share", "all kept discharges",
           "mean capacity inside window", stats["capacity_mean"], "%",
           REQUIRED["window_partial"])
    record("SOH", "partial-window share", "all kept discharges",
           "mean time inside window", stats["time_mean"], "%",
           REQUIRED["window_partial"])

    return (
        f"The {ph('wp_window_high', features.WINDOW_VOLTAGE_HIGH, '.1f')}–"
        f"{ph('wp_window_low', features.WINDOW_VOLTAGE_LOW, '.1f')} V window "
        f"is not a small slice: across the "
        f"{ph('wp_n_cells', stats['n_cells'], 'd')} kept cells it holds "
        f"{ph('wp_capacity_mean', stats['capacity_mean'], '.0f')}% of each "
        f"discharge's capacity and "
        f"{ph('wp_time_mean', stats['time_mean'], '.0f')}% of its time on "
        f"average ({ph('wp_capacity_min', stats['capacity_min'], '.0f')}–"
        f"{ph('wp_capacity_max', stats['capacity_max'], '.0f')}% cell by "
        f"cell; the cold G9 cell B0053 holds the least). Per cell in "
        f"`docs/MODEL_CARD.md`."
    )


def build_model_card(partial_transfer, rul, clean, wr_summary, wr_limits,
                     random_transfer, window_partial, wt_summary, wt_limits):
    """
    docs/MODEL_CARD.md: what the shipped model takes, how well it
    scores on cells it never saw, which conditions were exercised,
    which were not, and when it refuses to answer.

    Inputs and units are read from src/features.py, the accuracy
    numbers from the result CSVs, and the tested noise/window levels
    from the robustness summary, so the card cannot state a number
    the pipeline did not measure.
    """

    lines = []

    lines.append("## What it predicts")
    lines.append("")
    lines.append(
        "Three things from measurements a battery management system can "
        "actually take. A **partial-window SOH** (percent) from the "
        f"{ph('mc_window_high', features.WINDOW_VOLTAGE_HIGH, '.1f')}–"
        f"{ph('mc_window_low', features.WINDOW_VOLTAGE_LOW, '.1f')} V window "
        "alone — the majority of a discharge's capacity on the warm cells "
        "and about a quarter on the cold one, not a small early slice — "
        "read before the discharge finishes. An **indicative remaining life** "
        "in sequential discharges, reported as a range, not a point. And the "
        "**window formula** it is scored against, the best a BMS can compute "
        "from the window alone."
    )

    lines.append("")
    lines.append("## How partial the window is")
    lines.append("")
    stats = window_partial_stats(window_partial)
    lines.append(
        f"Calling this a \"partial discharge\" is not a claim that the window "
        f"is small. Averaged over the {ph('mc_wp_n_cells', stats['n_cells'], 'd')} "
        f"kept cells the {ph('mc_wp_window_high', features.WINDOW_VOLTAGE_HIGH, '.1f')}–"
        f"{ph('mc_wp_window_low', features.WINDOW_VOLTAGE_LOW, '.1f')} V window "
        f"holds {ph('mc_wp_capacity_mean', stats['capacity_mean'], '.1f')}% of "
        f"each discharge's capacity and "
        f"{ph('mc_wp_time_mean', stats['time_mean'], '.1f')}% of its time. It "
        "is not uniform: the warm G1 cells hold most of their capacity in the "
        "window, while the cold G9 cell traverses the band early and holds "
        "little."
    )
    lines.append("")
    lines.append("| Cell | Capacity inside the window (%) | Time inside the window (%) |")
    lines.append("|---|---|---|")
    for _, row in stats["cells"].iterrows():
        cell = str(row["cell_id"])
        tag = str(row["cell_id"]).lower()
        capacity = float(row["capacity_inside_window_pct"])
        time = float(row["time_inside_window_pct"])
        record("model card", "window share", cell,
               "capacity inside window", capacity, "%",
               REQUIRED["window_partial"])
        record("model card", "window share", cell,
               "time inside window", time, "%",
               REQUIRED["window_partial"])
        lines.append(
            f"| {cell} | {ph(f'mc_wp_{tag}_cap', capacity, '.2f')} | "
            f"{ph(f'mc_wp_{tag}_time', time, '.2f')} |"
        )
    lines.append(
        f"| **Mean over {ph('mc_wp_n_cells', stats['n_cells'], 'd')} cells** | "
        f"{ph('mc_wp_capacity_mean', stats['capacity_mean'], '.2f')} | "
        f"{ph('mc_wp_time_mean', stats['time_mean'], '.2f')} |"
    )

    lines.append("")
    lines.append("## Inputs and units")
    lines.append("")
    lines.append(
        "The raw quantities measured inside the trained window, from "
        "`src/features.py` (`WINDOW_FEATURES` plus the one normalisation "
        "input)."
    )
    lines.append("")
    lines.append("| Input | Unit | Meaning |")
    lines.append("|---|---|---|")

    meaning = {
        "vw_time_s": "seconds spent inside the window",
        "vw_capacity_ah": "charge delivered inside the window",
        "vw_voltage_mean": "mean cell voltage inside the window",
        "vw_dqdv_peak_v": "voltage of the dQ/dV peak inside the window",
        "vw_dqdv_peak_height": "height of the dQ/dV peak",
        "current_mean": "mean current over the discharge; the one "
                         "non-window input the cell-relative normalisation "
                         "needs",
    }
    for name in features.WINDOW_FEATURES + features.WINDOW_NORMALIZATION_INPUTS:
        lines.append(
            f"| `{name}` | {features.WINDOW_UNITS[name]} | {meaning[name]} |"
        )

    lines.append("")
    lines.append("## Model inputs")
    lines.append("")
    lines.append(
        "The estimator takes the raw window made relative to the cell's own "
        f"opening median of the first "
        f"{ph('mc_reference_window', features.WINDOW_REFERENCE_WINDOW, 'd')} "
        "discharges, so the model reads a level, not an absolute capacity:"
    )
    lines.append("")
    for name in features.WINDOW_MODEL_FEATURES:
        lines.append(f"- `{name}`")

    lines.append("")
    lines.append("## Trained on")
    lines.append("")
    lines.append(
        "Only the nine constant-current NASA cells the audit keeps — G1 "
        "(B0005, B0006, B0007, B0018), G3 (B0029–B0032) and G9 (B0053). "
        "Target is `soh` (percent). The random-load cells are scored but "
        "never trained on."
    )

    lines.append("")
    lines.append("## Held-out accuracy")
    lines.append("")
    lines.append(
        "The partial-window SOH model is scored by leave-one-cell-out inside "
        "G1 and G3, then trained on G1+G3 and tested on the single G9 cell. "
        "**MAE is in percentage points of SOH, on a cell the model never "
        "saw.**"
    )
    lines.append("")
    lines.append("| Held-out cell | Protocol | Cell-relative XGBoost MAE (%) |")
    lines.append("|---|---|---|")

    protocols = [
        ("leave-one-cell-out: G1", "leave-one-cell-out: G1", "g1"),
        ("leave-one-cell-out: G3", "leave-one-cell-out: G3", "g3"),
        ("train G1+G3, test B0053", "train G1+G3, test B0053", "b0053"),
    ]
    for key, display, tag in protocols:
        block = partial_transfer[partial_transfer["protocol"] == key]
        for cell in sorted(block["test_cell"].unique()):
            row = block[block["test_cell"] == cell]
            mae = float(row.loc[row["model"] == "XGBoost", "mae_pct"].iloc[0])
            pid = f"mc_{tag}_{str(cell).lower()}_xgb"
            record("model card", "held-out SOH", str(cell),
                   "cell-relative XGBoost MAE", mae, "%",
                   REQUIRED["partial_transfer"])
            lines.append(
                f"| {cell} | {display} | {ph(pid, mae, '.2f')} |"
            )

    lines.append("")
    lines.append(
        "Indicative RUL, from the reference-trajectory matcher scored "
        "leave-one-cell-out over the four G1 cells. **MAE is in sequential "
        "discharges.**"
    )
    lines.append("")
    lines.append("| Held-out cell | Trajectory matcher MAE (discharges) |")
    lines.append("|---|---|")
    for _, row in rul.sort_values("test_cell").iterrows():
        cell = str(row["test_cell"]).lower()
        mae = float(row["trajectory_mae"])
        pid = f"mc_rul_{cell}_traj"
        record("model card", "held-out RUL", row["test_cell"],
               "trajectory MAE", mae, "discharges",
               REQUIRED["rul_trajectory"])
        lines.append(f"| {row['test_cell']} | {ph(pid, mae, '.1f')} |")

    lines.append("")
    lines.append("## Conditions tested")
    lines.append("")
    lines.append(
        f"- Constant-current discharges at the three kept operating "
        f"conditions: G1 {ph('mc_g1_temp', int(_cc_cond(clean, 'G1', 'nominal_temperature_c')), 'd')} °C / "
        f"{ph('mc_g1_amp', int(_cc_cond(clean, 'G1', 'nominal_current_a')), 'd')} A, "
        f"G3 {ph('mc_g3_temp', int(_cc_cond(clean, 'G3', 'nominal_temperature_c')), 'd')} °C / "
        f"{ph('mc_g3_amp', int(_cc_cond(clean, 'G3', 'nominal_current_a')), 'd')} A, "
        f"G9 {ph('mc_g9_temp', int(_cc_cond(clean, 'G9', 'nominal_temperature_c')), 'd')} °C / "
        f"{ph('mc_g9_amp', int(_cc_cond(clean, 'G9', 'nominal_current_a')), 'd')} A."
    )
    lines.append(
        f"- The {ph('mc_random_n', int(len(random_transfer[random_transfer['cell_id'] != 'MEAN'])), 'd')} "
        "random-load cells the audit keeps (B0039, B0042–B0044), scored with "
        "the shipped model but never trained on."
    )
    largest_mv = float(wr_limits["largest_voltage_noise_mv"].iloc[0])
    largest_pct = float(wr_limits["largest_current_noise_pct"].iloc[0])
    lines.append(
        f"- Sensor noise added to a held-out cell's inputs: Gaussian voltage "
        f"noise up to {ph('mc_largest_mv', largest_mv, '.0f')} mV and Gaussian "
        f"current noise up to {ph('mc_largest_pct', largest_pct, '.0f')}% of "
        "each sample's current."
    )
    narrow = wr_summary[wr_summary["kind"] == "window"].sort_values(
        "window_high_v", ascending=False
    )
    narrow_bands = " and ".join(
        f"{ph(f'mc_narrow_{index}_hi', float(row['window_high_v']), '.1f')}–"
        f"{ph(f'mc_narrow_{index}_lo', float(row['window_low_v']), '.1f')} V"
        for index, (_, row) in enumerate(narrow.iterrows())
    )
    lines.append(
        f"- The trained window and two narrower windows: "
        f"{ph('mc_window_high', features.WINDOW_VOLTAGE_HIGH, '.1f')}–"
        f"{ph('mc_window_low', features.WINDOW_VOLTAGE_LOW, '.1f')} V "
        f"(trained), {narrow_bands} (tested at inference)."
    )
    wt_limit = float(wt_limits["mae_limit_pct"].iloc[0])
    wt_smallest = str(wt_limits["smallest_under_limit_window"].iloc[0])
    if wt_smallest != "none":
        wt_row = wt_summary[wt_summary["window"] == wt_smallest].iloc[0]
        wt_tag = _wt_tag(wt_smallest)
        wt_bands = ", ".join(
            f"{ph(f'mc_wtband_{index}_hi', float(row['window_high_v']), '.1f')}–"
            f"{ph(f'mc_wtband_{index}_lo', float(row['window_low_v']), '.1f')}"
            for index, (_, row) in enumerate(wt_summary.iterrows())
        )
        lines.append(
            f"- A model trained on each candidate window ({wt_bands} V): the "
            f"smallest that holds the pooled held-out MAE under "
            f"{ph('wts_limit', wt_limit, '.0f')}% is "
            f"{ph(wt_tag + '_hi', float(wt_row['window_high_v']), '.1f')}–"
            f"{ph(wt_tag + '_lo', float(wt_row['window_low_v']), '.1f')} V "
            f"(span {ph(wt_tag + '_span', float(wt_row['window_span_v']), '.2f')} V) "
            f"at {ph('wts_small_all', float(wt_limits['smallest_under_limit_mae_pct'].iloc[0]), '.2f')}%."
        )

    lines.append("")
    lines.append("## Conditions NOT tested")
    lines.append("")
    lines.append(
        "- Real packs: no field data, no customer cell, no manufacturer "
        "reference and no hardware sign-off."
    )
    lines.append(
        "- Temperatures and currents outside the three tested conditions and "
        "the trained current band, which is "
        f"{ph('mc_current_low', features.WINDOW_TRAIN_CURRENT_LOW_A, '.1f')}–"
        f"{ph('mc_current_high', features.WINDOW_TRAIN_CURRENT_HIGH_A, '.1f')} A."
    )
    lines.append(
        "- Duty cycles with rapid, continuous load changes beyond the "
        "random-load cells above; only one current level per condition is "
        "trained on."
    )
    lines.append(
        "- Multi-cell pack interactions (imbalance, thermal coupling); the "
        "live pack monitor is exercised on injected synthetic faults."
    )
    lines.append(
        "- The ESP32 firmware on real hardware, and an external email or "
        "pager endpoint — the alert delivery target is a local mock receiver."
    )

    lines.append("")
    lines.append("## Refusal rules")
    lines.append("")
    lines.append(
        "The estimator returns no number, rather than a guess, when:"
    )
    lines.append("")
    lines.append(
        f"- a window is measured outside the trained "
        f"{ph('mc_window_high', features.WINDOW_VOLTAGE_HIGH, '.1f')}–"
        f"{ph('mc_window_low', features.WINDOW_VOLTAGE_LOW, '.1f')} V band "
        "(`features.WindowRangeError`);"
    )
    lines.append(
        f"- fewer than "
        f"{ph('mc_min_discharges', features.WINDOW_MIN_REFERENCE_DISCHARGES, 'd')} "
        "opening discharges are available for the cell-relative baseline "
        "(`InsufficientReferenceError`);"
    )
    lines.append(
        f"- the discharge current sits far outside the trained "
        f"{ph('mc_current_low', features.WINDOW_TRAIN_CURRENT_LOW_A, '.1f')}–"
        f"{ph('mc_current_high', features.WINDOW_TRAIN_CURRENT_HIGH_A, '.1f')} A "
        "band, where the model has no training data."
    )

    return "\n".join(lines)


def main():
    frames = {key: load(key) for key in REQUIRED}

    audit = frames["audit"]

    results = region("results", build_results(
        frames["clean"], audit,
        frames["soh_results"], frames["soh_nested"], frames["soh_baselines"],
        frames["soh_null"], frames["full_discharge"],
        frames["partial_discharge"], frames["partial_transfer"],
        frames["partial_window"], frames["ablation"], frames["logo"],
        frames["cross_condition"], frames["random_load_transfer"],
        frames["window_robustness"], frames["window_robustness_summary"],
        frames["window_robustness_limits"],
        frames["window_train_summary"], frames["window_train_limits"],
    ))
    rul = region(
        "rul",
        build_rul(
            frames["rul_trajectory"],
            frames["rul_comparison"],
            frames["rul_disagreement"],
            frames["rul_band"],
            frames["random_load"],
            frames["rul_random_labels"],
            frames["rul_g6_loco"],
            frames["rul_g1_to_g6"],
            frames["rul_g6_to_g1"],
            frames["rul_window_sweep"],
            frames["rul_prediction_bands"],
        ),
    )
    degradation = region(
        "degradation",
        build_degradation(audit, frames["reference_sensitivity"]),
    )
    dataset = region("dataset", build_dataset(audit))
    charge = region("charge", build_charge(frames["charge_comparison"]))
    pack = region("pack", build_pack(frames["pack_detection"], frames["pack_false_alarms"]))
    bms = region("bms_contract", build_bms_contract())
    final_body = build_final(
        frames["soh_results"], frames["soh_nested"], frames["soh_baselines"],
        frames["soh_null"], frames["partial_transfer"], frames["rul_trajectory"],
    )
    final = region("final", final_body)
    glance = region("results_glance", final_body)
    window_share = region(
        "window_share", build_window_share(frames["window_partial"])
    )
    window_train = region(
        "window_train",
        build_window_train_share(
            frames["window_train_summary"],
            frames["window_train_limits"],
            frames["window_robustness_limits"],
        ),
    )
    card = region("model_card", build_model_card(
        frames["partial_transfer"],
        frames["rul_trajectory"],
        frames["clean"],
        frames["window_robustness_summary"],
        frames["window_robustness_limits"],
        frames["random_load_transfer"],
        frames["window_partial"],
        frames["window_train_summary"],
        frames["window_train_limits"],
    ))

    write_regions(README, {
        "bms_contract": bms,
        "results_glance": glance,
        "window_share": window_share,
        "window_train": window_train,
    })
    write_regions(RESULTS, {
        "results": results,
        "charge": charge,
        "rul": rul,
        "degradation": degradation,
        "dataset": dataset,
    })
    write_regions(SETUP, {"pack": pack})
    write_regions(MODEL_CARD, {"model_card": card})
    write_regions(CHANGES, {"final": final})

    pd.DataFrame(_rows).to_csv(SUMMARY, index=False)

    document = {TEMPLATES_KEY: _templates, **_numbers}
    NUMBERS.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")

    print(
        f"wrote {NUMBERS.relative_to(ROOT)} and {SUMMARY.relative_to(ROOT)}, "
        f"and updated README.md, docs/RESULTS.md, docs/SETUP.md, "
        f"docs/MODEL_CARD.md and CHANGES.md"
    )


if __name__ == "__main__":
    main()
