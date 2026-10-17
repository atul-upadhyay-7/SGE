from pathlib import Path
import sys

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from data_cleaning import REFERENCE_RULE, reference_capacity  # noqa: E402


AUDIT = Path("data/processed/cell_audit.csv")
CLEAN = Path("data/processed/nasa_ml_dataset_clean.csv")


def test_audit_exists_and_has_required_columns():
    audit = pd.read_csv(AUDIT)
    required = [
        "cell_id", "group", "discharges", "first_capacity",
        "min_capacity", "last_capacity", "below50_median_count",
        "upward_jumps_gt3pp", "soh_max", "soh_min",
        "soh_reaches_80", "keep", "reason",
    ]
    for c in required:
        assert c in audit.columns


def test_known_bad_excluded():
    audit = pd.read_csv(AUDIT)
    bad = {
        "B0033", "B0034", "B0036", "B0045", "B0046",
        "B0047", "B0048", "B0054", "B0055", "B0056",
    }
    for b in bad:
        row = audit[audit["cell_id"] == b]
        assert not row.empty
        assert row["keep"].iloc[0] == False  # noqa: E712


def test_g1_jump_counter_is_nonzero():
    """Requirement 2.2: the SOH-jump audit must fire for G1."""

    audit = pd.read_csv(AUDIT)
    g1 = audit[audit["group"] == "G1"]
    assert not g1.empty
    assert (g1["upward_jumps_gt3pp"] > 0).all()


def test_limited_ageing_cells_are_labelled_not_silently_capped():
    """Requirement 2.3: B0029-B0032 and B0053 either excluded or
    labelled, and never truncated to a fake 100%."""

    audit = pd.read_csv(AUDIT)
    for cell in ["B0029", "B0030", "B0031", "B0032", "B0053"]:
        row = audit[audit["cell_id"] == cell].iloc[0]
        assert row["keep"] == True  # noqa: E712
        assert row["reason"] == "limited ageing, no RUL"


def test_saved_soh_matches_audit_soh():
    """The exclusion decision and the saved file must agree."""

    audit = pd.read_csv(AUDIT)
    clean = pd.read_csv(CLEAN)

    for _, row in audit.iterrows():
        cell = row["cell_id"]
        sub = clean[clean["cell_id"] == cell]
        if not row["keep"]:
            assert sub.empty
            continue
        assert not sub.empty
        assert abs(sub["soh"].max() - row["soh_max"]) < 1e-6


def test_reference_capacity_is_not_a_measurement_artifact():
    """
    A kept cell's reference (median of its first five valid discharges)
    must not sit under half the median of its later valid discharges.
    If it does, the reference was set by a corrupt opening block rather
    than a real beginning-of-life capacity, and every SOH value for
    that cell would be scaled by a bad number. B0041 is exactly this
    case (its opening runs are a measurement failure) and is why it is
    excluded rather than kept.
    """

    audit = pd.read_csv(AUDIT)
    clean = pd.read_csv(CLEAN)

    checked = 0

    for _, row in audit[audit["keep"].astype(bool)].iterrows():

        cap = (
            clean[clean["cell_id"] == row["cell_id"]]
            .sort_values("cycle")["capacity_ah"]
            .astype(float)
            .reset_index(drop=True)
        )
        assert len(cap) >= 5, f"{row['cell_id']}: too few rows"

        reference = reference_capacity(cap, REFERENCE_RULE)
        later = cap.iloc[len(cap) // 2:]

        assert reference >= 0.5 * later.median(), (
            f"{row['cell_id']}: reference {reference:.4f} Ah is under half the "
            f"median of its later valid discharges ({later.median():.4f} Ah); "
            "the reference is a measurement artifact, not a real capacity"
        )
        checked += 1

    assert checked >= 8, "expected the kept cells to be checked"


def test_no_excluded_cell_appears_downstream():
    """Requirement 2.4: excluded cells must not leak into the clean
    dataset or the reference curves file."""

    audit = pd.read_csv(AUDIT)
    excluded = set(
        audit.loc[~audit["keep"].astype(bool), "cell_id"]
    )
    assert excluded

    clean = pd.read_csv(CLEAN)
    assert excluded.isdisjoint(set(clean["cell_id"].unique()))

    curves = Path("models/rul_reference_curves.json")
    if curves.exists():
        import json

        data = json.loads(curves.read_text())
        assert excluded.isdisjoint(set(data.get("curves", {})))
