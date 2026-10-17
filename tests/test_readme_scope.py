"""
test_readme_scope.py
--------------------
The README must say what the project is not, and must state the
measurement conditions the partial-window model needs before it will
score a discharge.

Those conditions are the model's own contract, so this test reads
them from src/features.py and asserts the generated "What a real BMS
must provide" region states the same numbers. The section cannot
drift from the code that enforces it: renaming a constant in
features.py fails make_report.py (which imports it) and a stale
threshold typed into the README fails scripts/check_numbers.py, and
this test fails if the two ever disagree.
"""

from pathlib import Path

import features

README = Path(__file__).resolve().parent.parent / "README.md"


def region_text(name):
    text = README.read_text()
    start = f"<!-- make_report:{name} -->"
    end = f"<!-- make_report:{name}:end -->"
    assert start in text and end in text, f"missing {name} region markers"
    return text.split(start, 1)[1].split(end, 1)[0]


def test_readme_declares_what_it_is_not():
    text = README.read_text()

    assert "## What this is not" in text
    assert "## What a real BMS must provide" in text

    section = text.split("## What this is not", 1)[1]
    section = section.split("## What a real BMS", 1)[0].lower()

    assert "not our own packs" in section
    assert "field validation" in section
    assert "indicative" in section


def test_bms_contract_region_states_the_features_thresholds():
    region = region_text("bms_contract")

    assert f"{features.WINDOW_VOLTAGE_HIGH:.1f}" in region
    assert f"{features.WINDOW_VOLTAGE_LOW:.1f}" in region

    assert f"first {features.WINDOW_REFERENCE_WINDOW}" in region
    assert f"fewer than {features.WINDOW_MIN_REFERENCE_DISCHARGES}" in region

    assert (
        f"{features.WINDOW_TRAIN_CURRENT_LOW_A:.1f}-"
        f"{features.WINDOW_TRAIN_CURRENT_HIGH_A:.1f} A"
    ) in region


def test_bms_contract_thresholds_live_in_features():
    """The generator reads these names, so they must exist here."""

    for name in (
        "WINDOW_VOLTAGE_HIGH",
        "WINDOW_VOLTAGE_LOW",
        "WINDOW_REFERENCE_WINDOW",
        "WINDOW_MIN_REFERENCE_DISCHARGES",
        "WINDOW_TRAIN_CURRENT_LOW_A",
        "WINDOW_TRAIN_CURRENT_HIGH_A",
    ):
        assert isinstance(getattr(features, name), (int, float))
