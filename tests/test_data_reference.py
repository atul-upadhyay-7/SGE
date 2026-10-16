"""
test_data_reference.py
----------------------
The data-quality re-reference study must match the report's claims.

These tests read the artifacts the pipeline writes and check:

  * the production reference rule (first-five median) reproduces the
    committed keep/exclude decision and SOH exactly;
  * the gentler rule the report starts from (median of discharges
    3-8) returns no excluded cell;
  * no G4 cell returns under any reference rule, because its capacity
    genuinely rises across the record;
  * the beginning-of-life peak returns exactly the four G7 cells;
  * a reference change never drops a cell the production rule keeps.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import data_cleaning as DC  # noqa: E402

SENSITIVITY = REPO / "data" / "processed" / "data_reference_sensitivity.csv"
AUDIT = REPO / "data" / "processed" / "cell_audit.csv"

G4 = ("B0033", "B0034", "B0036")
G7 = ("B0045", "B0046", "B0047", "B0048")
G9 = ("B0054", "B0055", "B0056")


def _load(path):
    if not path.exists():
        pytest.skip(f"data_cleaning.py has not produced {path.name}")
    return pd.read_csv(path)


@pytest.fixture(scope="module")
def sensitivity():
    return _load(SENSITIVITY)


@pytest.fixture(scope="module")
def audit():
    return _load(AUDIT)


def _by_rule(sensitivity, cell):
    return sensitivity[sensitivity["cell_id"] == cell].set_index(
        "reference_rule"
    )


def test_production_rule_reproduces_the_audit(sensitivity, audit):
    """The first-five-median rows must equal the committed decision."""
    strict = sensitivity[
        sensitivity["reference_rule"] == DC.REFERENCE_RULE
    ].set_index("cell_id")
    merged = audit.set_index("cell_id")
    assert set(strict.index) == set(merged.index)
    assert (strict["keep"] == merged["keep"]).all()
    assert np.allclose(strict["soh_max"], merged["soh_max"], rtol=1e-9)


def test_suggested_gentler_rule_returns_nothing(sensitivity):
    """The report says the rule it started from (median 3-8) returns
    no excluded cell."""
    strict = sensitivity[
        sensitivity["reference_rule"] == DC.REFERENCE_RULE
    ].set_index("cell_id")
    mid = sensitivity[
        sensitivity["reference_rule"] == "median_3_8"
    ].set_index("cell_id")
    excluded = strict[~strict["keep"]].index
    returned = [c for c in excluded if bool(mid.loc[c, "keep"])]
    assert returned == [], (
        f"median 3-8 now returns {returned}; the report claim needs revisiting"
    )


def test_no_g4_cell_returns_under_any_rule(sensitivity):
    """The guardrail: cells whose capacity genuinely rises stay out."""
    for cell in G4:
        rows = _by_rule(sensitivity, cell)
        assert not rows["keep"].any(), f"{cell} returned under {list(rows.index)}"


def test_bol_peak_returns_exactly_g7(sensitivity):
    strict = sensitivity[
        sensitivity["reference_rule"] == DC.REFERENCE_RULE
    ].set_index("cell_id")
    peak = sensitivity[
        sensitivity["reference_rule"] == "first5_max"
    ].set_index("cell_id")
    excluded = strict[~strict["keep"]].index
    returned = sorted(c for c in excluded if bool(peak.loc[c, "keep"]))
    assert returned == list(G7), (
        f"BOL-peak returned {returned}, expected G7 only"
    )


def test_g9_cells_stay_out_under_every_rule(sensitivity):
    for cell in G9:
        rows = _by_rule(sensitivity, cell)
        assert not rows["keep"].any(), (
            f"{cell} returned under {list(rows[rows['keep']].index)}"
        )


def test_a_reference_change_never_drops_a_kept_cell(sensitivity):
    strict = sensitivity[
        sensitivity["reference_rule"] == DC.REFERENCE_RULE
    ].set_index("cell_id")
    kept = set(strict[strict["keep"]].index)
    for rule in DC.SENSITIVITY_RULES:
        rows = sensitivity[sensitivity["reference_rule"] == rule]
        dropped = [
            c
            for c in kept
            if not bool(rows[rows["cell_id"] == c]["keep"].iloc[0])
        ]
        assert dropped == [], f"{rule} dropped kept cells {dropped}"
