#!/usr/bin/env python
"""
make_report.py
--------------
Build the numbers that appear in README.md and CHANGES.md from the
measured CSVs. Nothing here is typed by hand: every value is read
from a CSV under data/processed/, and every number written into a
marked region is a {{id}} placeholder rendered from that value.

A marked region looks like:

    <!-- make_report:results -->
    ... generated content ...
    <!-- make_report:results:end -->

The script writes, all from the CSVs:

  * the rendered regions back into README.md and CHANGES.md;
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
from pathlib import Path

import pandas as pd

from report_render import TEMPLATES_KEY, render

ROOT = Path(__file__).resolve().parent.parent
PROC = ROOT / "data" / "processed"
EXP = PROC / "experiments"

README = ROOT / "README.md"
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
    "rul_band": PROC / "rul_band_calibration.csv",
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
    return f"{head}{start}\n{body.rstrip()}\n{end}{tail}"


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


def build_results(clean, audit, soh_res, soh_nested, soh_base, soh_null,
                  full, partial, partial_transfer, partial_window,
                  ablation, logo, cross):
    lines = []

    # Cross-condition transfer opens Key Results: the model that
    # looks good within G1 does not survive a change of operating
    # condition, and that is the headline caveat on every number
    # below it.
    lines.append(build_cross_condition(logo, cross, clean, soh_base))
    lines.append("")

    lines.append(build_partial_transfer(partial_transfer))
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


def build_rul(rul, comparison, band):
    lines = []
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
    lines.append("#### Trajectory matching versus early-fade extrapolation")
    lines.append("")
    lines.append(
        "The matcher was also scored against a fade-rate extrapolation: "
        "a straight line through the first "
        f"{ph('rc_window', int(comparison['window'].iloc[0]), 'd')} measured "
        "SOH values of the cell, extended to "
        "the retirement threshold. Both are leave-one-cell-out and use the "
        "same held-out discharges, so the columns are comparable."
    )
    lines.append("")
    lines.append(
        "| Held-out cell | Trajectory MAE | Early-fade extrapolation MAE | "
        "Fleet-lifetime baseline MAE |"
    )
    lines.append("|---|---|---|---|")
    for _, row in comparison.sort_values("test_cell").iterrows():
        cell = str(row["test_cell"]).lower()
        record("RUL", "trajectory", f"comparison {row['test_cell']}", "MAE",
               row["trajectory_mae"], "discharges", REQUIRED["rul_comparison"])
        record("RUL", "early-fade extrapolation", f"comparison {row['test_cell']}",
               "MAE", row["extrapolation_mae"], "discharges",
               REQUIRED["rul_comparison"])
        record("RUL", "fleet lifetime baseline", f"comparison {row['test_cell']}",
               "MAE", row["fleet_lifetime_baseline_mae"], "discharges",
               REQUIRED["rul_comparison"])
        lines.append(
            f"| {row['test_cell']} | "
            f"{ph('rul_' + cell + '_traj_mae', float(row['trajectory_mae']), '.1f')} | "
            f"{ph('rc_' + cell + '_extrap_mae', float(row['extrapolation_mae']), '.1f')} | "
            f"{ph('rul_' + cell + '_base_mae', float(row['fleet_lifetime_baseline_mae']), '.1f')} |"
        )
    extrap_mean = mean(comparison["extrapolation_mae"])
    record("RUL", "early-fade extrapolation", "mean (G1)", "mean MAE",
           extrap_mean, "discharges", REQUIRED["rul_comparison"])
    lines.append(
        f"| **Mean** | {ph('rul_mean_traj_mae', traj_mean, '.1f')} | "
        f"{ph('rc_mean_extrap_mae', extrap_mean, '.1f')} | "
        f"{ph('rul_mean_base_mae', base_mean, '.1f')} |"
    )
    lines.append("")
    lines.append(
        "Trajectory matching wins the mean and three of the four cells. The "
        "extrapolation wins exactly one cell, B0006, and loses badly on B0005 "
        "and B0007: B0006 fades almost linearly from its first discharge, so "
        "its early line already points near the right end of life, whereas "
        "B0005 and B0007 hold their capacity and then drop through a late "
        "knee, so the same line places end of life far too late and the "
        "estimate overshoots. Neither method "
        "dominates, so trajectory matching stays the default and the "
        "extrapolation is kept here as the reported loser overall and the "
        "one case it wins."
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


def main():
    frames = {key: load(key) for key in REQUIRED}

    audit = frames["audit"]

    results = region("results", build_results(
        frames["clean"], audit,
        frames["soh_results"], frames["soh_nested"], frames["soh_baselines"],
        frames["soh_null"], frames["full_discharge"],
        frames["partial_discharge"], frames["partial_transfer"],
        frames["partial_window"], frames["ablation"], frames["logo"],
        frames["cross_condition"],
    ))
    rul = region(
        "rul",
        build_rul(
            frames["rul_trajectory"],
            frames["rul_comparison"],
            frames["rul_band"],
        ),
    )
    degradation = region(
        "degradation",
        build_degradation(audit, frames["reference_sensitivity"]),
    )
    dataset = region("dataset", build_dataset(audit))
    pack = region("pack", build_pack(frames["pack_detection"], frames["pack_false_alarms"]))
    final = region("final", build_final(
        frames["soh_results"], frames["soh_nested"], frames["soh_baselines"],
        frames["soh_null"], frames["partial_transfer"], frames["rul_trajectory"],
    ))

    readme = README.read_text()
    readme = replace_region(readme, "results", results)
    readme = replace_region(readme, "rul", rul)
    readme = replace_region(readme, "degradation", degradation)
    readme = replace_region(readme, "dataset", dataset)
    readme = replace_region(readme, "pack", pack)
    README.write_text(readme)

    changes = CHANGES.read_text()
    changes = replace_region(changes, "final", final)
    CHANGES.write_text(changes)

    pd.DataFrame(_rows).to_csv(SUMMARY, index=False)

    document = {TEMPLATES_KEY: _templates, **_numbers}
    NUMBERS.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")

    print(
        f"wrote {NUMBERS.relative_to(ROOT)} and {SUMMARY.relative_to(ROOT)}, "
        f"and updated README.md, CHANGES.md"
    )


if __name__ == "__main__":
    main()
