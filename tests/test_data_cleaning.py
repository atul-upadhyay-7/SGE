from pathlib import Path
import pandas as pd


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
