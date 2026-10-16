"""
test_contract_doc.py
--------------------
docs/CONTRACT.md is generated from src/features.py, so the committed
copy must equal what the generator produces. These tests re-render it
in memory (they never write the file) and fail if the two drift, if a
window feature or unit is missing from the document, or if the model
file and refusal names it promises are not actually named.
"""

import importlib.util
from pathlib import Path

import features

ROOT = Path(__file__).resolve().parent.parent

CONTRACT = ROOT / "docs" / "CONTRACT.md"
GENERATOR = ROOT / "docs" / "make_contract.py"


def _generator():

    spec = importlib.util.spec_from_file_location(
        "make_contract", GENERATOR
    )

    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    return module


def test_contract_matches_the_generator():
    """The committed document is exactly the generator's output."""

    assert CONTRACT.exists(), (
        "docs/CONTRACT.md is missing; run: python docs/make_contract.py"
    )

    module = _generator()

    assert CONTRACT.read_text() == module.render(), (
        "docs/CONTRACT.md has drifted from src/features.py; "
        "run: python docs/make_contract.py"
    )


def test_contract_lists_every_window_input_unit_and_model_input():
    """Nothing in the contract can be documented by hand: every raw
    input, its unit and every model input comes from src/features.py."""

    text = CONTRACT.read_text()

    raw_inputs = (
        list(features.WINDOW_FEATURES)
        + list(features.WINDOW_NORMALIZATION_INPUTS)
    )

    for name in raw_inputs:

        assert f"`{name}`" in text, f"{name} is not in the contract"
        assert features.WINDOW_UNITS[name] in text, (
            f"{name} unit is not in the contract"
        )

    for name in features.WINDOW_MODEL_FEATURES:

        assert f"`{name}`" in text, (
            f"model input {name} is not in the contract"
        )


def test_contract_names_the_model_files_the_payload_and_the_refusals():
    """The four things a backend caller needs to look up."""

    text = CONTRACT.read_text()

    assert "models/soh_window_all.ubj" in text
    assert "models/soh_window_all.joblib" in text
    assert "models/soh_window_all.json" in text

    assert "predict_soh" in text
    assert "window_features" in text
    assert "early_discharges" in text

    assert "WindowRangeError" in text
    assert "InsufficientReferenceError" in text


def test_contract_is_marked_generated():
    """A reader must be told not to edit it, and how to rebuild it."""

    head = CONTRACT.read_text().splitlines()[:5]

    joined = "\n".join(head)

    assert "generated" in joined.lower()
    assert "docs/make_contract.py" in joined
