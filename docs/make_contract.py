"""
make_contract.py
----------------
Render docs/CONTRACT.md from the canonical definitions.

The backend and ML interfaces share one source of truth,
src/features.py: the window feature names, their units and the model
input list are defined there and imported by both the offline pipeline
and backend/app/ml/estimator.py. This script turns those definitions
into a human-readable contract so neither side is documented by hand.

Run it after any change to src/features.py or to the model bundle:

    python docs/make_contract.py

tests/test_contract_doc.py re-renders the document in memory and fails
if the committed copy drifts from what the definitions produce, so a
hand-edit or a missing regeneration is caught in the suite. Like
docs/make_architecture.py this is a committed-document generator, not
a pipeline stage, so the pipeline never rewrites it.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if str(ROOT / "src") not in sys.path:

    sys.path.insert(0, str(ROOT / "src"))

import features  # noqa: E402

OUTPUT = ROOT / "docs" / "CONTRACT.md"

MODEL_JSON = ROOT / "models" / "soh_window_all.json"


class ContractError(RuntimeError):
    """The definitions and this document no longer agree."""


# One description per model input. render() requires exact coverage of
# features.WINDOW_MODEL_FEATURES, so adding a model input without
# describing it here fails the run rather than emitting a blank row.
MODEL_INPUT_DEFINITIONS = {
    "vw_capacity_ratio":
        "window `vw_capacity_ah` over the cell's opening median of "
        "`vw_capacity_ah`",
    "vw_capacity_per_amp":
        "window `vw_capacity_ah` divided by `abs(current_mean)` "
        "(scale-free; removes the 2 A versus 4 A rate difference)",
    "vw_time_ratio":
        "window `vw_time_s` over the cell's opening median of "
        "`vw_time_s`",
    "vw_voltage_ratio":
        "window `vw_voltage_mean` over the cell's opening median of "
        "`vw_voltage_mean`",
    "vw_dqdv_height_ratio":
        "window `vw_dqdv_peak_height` over the cell's opening median "
        "of `vw_dqdv_peak_height`",
}

# One description per raw input, including the normalisation input.
RAW_INPUT_DESCRIPTIONS = {
    "vw_time_s": "seconds spent inside the 4.0-3.4 V window",
    "vw_capacity_ah": "charge delivered inside the window",
    "vw_voltage_mean": "mean cell voltage inside the window",
    "vw_dqdv_peak_v": "voltage of the IC (dQ/dV) peak inside the window",
    "vw_dqdv_peak_height": "height of the IC peak",
    "current_mean":
        "mean current over the discharge; the one non-window input the "
        "cell-relative normalisation needs",
}


def _load_model_bundle():
    """Read the committed bundle metadata and check it against the
    canonical model input list."""

    if not MODEL_JSON.exists():

        raise ContractError(
            f"model bundle metadata not found at {MODEL_JSON}. Train it "
            "with:  python src/train_window_soh.py"
        )

    bundle = json.loads(MODEL_JSON.read_text())

    expected = list(features.WINDOW_MODEL_FEATURES)

    if list(bundle.get("features", [])) != expected:

        raise ContractError(
            "models/soh_window_all.json was trained on a different "
            f"feature list ({bundle.get('features')}) than "
            f"features.WINDOW_MODEL_FEATURES ({expected}). Retrain the "
            "bundle or fix src/features.py so the two agree."
        )

    return bundle


def _table(header, rows):
    """A GitHub-flavoured markdown table from header and row lists."""

    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]

    for row in rows:

        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


def _raw_input_rows():

    names = (
        list(features.WINDOW_FEATURES)
        + list(features.WINDOW_NORMALIZATION_INPUTS)
    )

    missing = [name for name in names if name not in features.WINDOW_UNITS]

    if missing:

        raise ContractError(
            f"WINDOW_UNITS has no unit for {missing} in src/features.py"
        )

    described = set(RAW_INPUT_DESCRIPTIONS)

    if set(names) != described:

        raise ContractError(
            "RAW_INPUT_DESCRIPTIONS no longer matches the raw inputs: "
            f"missing {sorted(set(names) - described)}, extra "
            f"{sorted(described - set(names))}"
        )

    return [
        [
            f"`{name}`",
            features.WINDOW_UNITS[name],
            RAW_INPUT_DESCRIPTIONS[name],
        ]
        for name in names
    ]


def _model_input_rows():

    names = list(features.WINDOW_MODEL_FEATURES)

    described = set(MODEL_INPUT_DEFINITIONS)

    if set(names) != described:

        raise ContractError(
            "MODEL_INPUT_DEFINITIONS no longer matches "
            "WINDOW_MODEL_FEATURES: missing "
            f"{sorted(set(names) - described)}, extra "
            f"{sorted(described - set(names))}"
        )

    return [[f"`{name}`", MODEL_INPUT_DEFINITIONS[name]] for name in names]


def render():
    """Return the full contract document as one string."""

    bundle = _load_model_bundle()

    high = features.WINDOW_VOLTAGE_HIGH
    low = features.WINDOW_VOLTAGE_LOW
    tol = features.WINDOW_VOLTAGE_TOL

    band_high = f"{high:.1f}"
    band_low = f"{low:.1f}"

    parts = []

    parts.append(
        "# Backend and ML contract\n"
        "\n"
        "<!-- generated from src/features.py by docs/make_contract.py; "
        "do not edit by hand -->\n"
        "<!-- regenerate: python docs/make_contract.py -->\n"
        "\n"
        "This document is generated, not written: `docs/make_contract.py`\n"
        "reads the canonical definitions from `src/features.py` and\n"
        "`models/soh_window_all.json` and renders them here. Feature\n"
        "names, units and the model input list therefore have exactly one\n"
        "source, and `tests/test_contract_doc.py` fails if this committed\n"
        "copy drifts from it.\n"
    )

    parts.append(
        "## Partial-window inputs\n"
        "\n"
        "The partial-window SOH model scores one discharge from the\n"
        f"window a BMS can always see — the {band_high} V down to\n"
        f"{band_low} V span every discharge crosses\n"
        "(`WINDOW_VOLTAGE_HIGH` / `WINDOW_VOLTAGE_LOW`), which is most\n"
        "of a warm cell's capacity and not a small early slice — plus\n"
        "the cell's opening discharges. A\n"
        "window measurement outside that band is refused rather than\n"
        "scored (see *Refusals* below).\n"
        "\n"
        + _table(
            ["Input", "Unit", "Meaning"],
            _raw_input_rows(),
        )
        + "\n"
    )

    parts.append(
        "## Model inputs\n"
        "\n"
        "The estimator consumes the raw window made relative to the\n"
        "cell's own opening baseline, not the raw level. These are the\n"
        "model inputs, in the order the bundle stores them\n"
        "(`WINDOW_MODEL_FEATURES`):\n"
        "\n"
        + _table(
            ["Model input", "Definition"],
            _model_input_rows(),
        )
        + "\n"
        "\n"
        "The baseline is the median of the cell's first\n"
        f"`WINDOW_REFERENCE_WINDOW` = {features.WINDOW_REFERENCE_WINDOW} "
        "valid discharges of each quantity; a cell with fewer than\n"
        f"`WINDOW_MIN_REFERENCE_DISCHARGES` = "
        f"{features.WINDOW_MIN_REFERENCE_DISCHARGES} opening discharges\n"
        "is refused, because the baseline would be a guess.\n"
    )

    parts.append(
        "## Model file\n"
        "\n"
        + _table(
            ["Property", "Value"],
            [
                ["Portable model", "`models/soh_window_all.ubj` (UBJ)"],
                ["Joblib copy", "`models/soh_window_all.joblib`"],
                ["Bundle metadata", "`models/soh_window_all.json`"],
                [
                    "Input features",
                    ", ".join(f"`{f}`" for f in bundle["features"]),
                ],
                ["Target", f"`{bundle.get('target', 'soh')}` (percent)"],
                [
                    "Trained on",
                    ", ".join(f"`{c}`" for c in bundle.get("trained_on", [])),
                ],
                [
                    "Library versions",
                    ", ".join(
                        f"{name} {version}"
                        for name, version in sorted(
                            bundle.get("library_versions", {}).items()
                        )
                    ),
                ],
            ],
        )
        + "\n"
        "\n"
        "The three files are one model: the UBJ is what the estimator\n"
        "loads, the joblib is the reference serialisation the trainer\n"
        "writes, and the JSON carries the metadata above. Its feature\n"
        "list must equal `WINDOW_MODEL_FEATURES`; if it does not, the\n"
        "model was trained on a different contract.\n"
    )

    example_window = {
        "vw_time_s": 1234.5,
        "vw_capacity_ah": 0.62,
        "vw_voltage_mean": 3.71,
        "vw_dqdv_peak_v": 3.66,
        "vw_dqdv_peak_height": 0.012,
        "current_mean": -2.0,
    }

    parts.append(
        "## Estimator payload\n"
        "\n"
        "`backend/app/ml/estimator.py::predict_soh` is the backend entry\n"
        "point. It takes the window for the discharge being scored and\n"
        "the cell's opening discharges. A minimal payload, with\n"
        "illustrative values:\n"
        "\n"
        "```json\n"
        + json.dumps(
            {
                "window_features": example_window,
                "early_discharges": [example_window],
            },
            indent=2,
        )
        + "\n```\n"
        "\n"
        "* `window_features` carries exactly the six names in the\n"
        "  *Partial-window inputs* table above (`WINDOW_FEATURES` plus\n"
        "  `WINDOW_NORMALIZATION_INPUTS`), with units as listed.\n"
        "* `early_discharges` is the cell's opening discharges in order;\n"
        f"  at least {features.WINDOW_MIN_REFERENCE_DISCHARGES} are "
        "required, and the baseline is the median of the first\n"
        f"  {features.WINDOW_REFERENCE_WINDOW} of them.\n"
        "* The response is one number: the estimated SOH in percent.\n"
        "\n"
        "### Refusals\n"
        "\n"
        "* `features.WindowRangeError` — `vw_voltage_mean` is present\n"
        f"  but outside {band_low} to {band_high} V (with {tol:g} V slack for\n"
        "  sensor resolution). A window measured over a different span\n"
        "  has no training data and is never scored.\n"
        "* `estimator.InsufficientReferenceError` — fewer than\n"
        f"  {features.WINDOW_MIN_REFERENCE_DISCHARGES} opening "
        "discharges were supplied.\n"
    )

    parts.append(
        "## Backend ingestion payload (reference)\n"
        "\n"
        "The telemetry contract the backend ingests is separate from the\n"
        "ML estimator above and is defined in\n"
        "`backend/app/schemas/telemetry.py` (`TelemetryPayload`), not in\n"
        "`src/features.py`. It is reproduced here only so the two\n"
        "interfaces are documented in one place; that module remains its\n"
        "source of truth:\n"
        "\n"
        + _table(
            ["Field", "Type", "Notes"],
            [
                ["`schema_version`", "int >= 1", "payload schema version"],
                ["`message_id`", "string", "idempotency key"],
                ["`device_id`", "string", "registered device"],
                ["`measured_at`", "datetime", "must be timezone-aware (UTC)"],
                ["`current`", "float", "amperes (discharge negative)"],
                ["`temperature`", "float", "degrees Celsius"],
                [
                    "`cell_voltages`",
                    "list[float]",
                    "non-empty and finite",
                ],
                [
                    "`pack_voltage`",
                    "float or null",
                    "optional",
                ],
            ],
        )
        + "\n"
        "\n"
        "Unknown fields are preserved in `additional_data` rather than\n"
        "rejected. A legacy single-cell frame (a `voltage` and no\n"
        "`cell_voltages`) is adapted onto this contract.\n"
    )

    parts.append(
        "## Full-discharge feature set (offline model)\n"
        "\n"
        "The offline SOH model trains on a wider list, `features.FEATURES`.\n"
        "It is listed here for completeness; only the window inputs above\n"
        "have units defined in `src/features.py`, and only the window\n"
        "model is exposed by the backend.\n"
        "\n"
        + "\n".join(f"* `{name}`" for name in features.FEATURES)
        + "\n"
        "\n"
        "These columns are target-derived or forward-looking and are\n"
        "never model inputs (`features.LEAKY_FEATURES`); `make_xy`\n"
        "refuses to build a matrix that contains them:\n"
        "\n"
        + "\n".join(f"* `{name}`" for name in features.LEAKY_FEATURES)
        + "\n"
    )

    return "\n".join(parts)


def main(argv=None):

    document = render()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(document)

    print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(document)} bytes)")

    return 0


if __name__ == "__main__":

    sys.exit(main())
