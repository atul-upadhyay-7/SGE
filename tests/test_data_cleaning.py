from pathlib import Path
import pandas as pd

def test_audit_exists_and_has_required_columns():
    audit = pd.read_csv(Path("data/processed/cell_audit.csv"))
    required = ["cell_id", "group", "discharges", "first_capacity", "min_capacity",
                "last_capacity", "below50_median_count", "upward_jumps_gt3",
                "soh_reaches_80", "keep", "reason"]
    for c in required:
        assert c in audit.columns


def test_known_bad_excluded():
    audit = pd.read_csv(Path("data/processed/cell_audit.csv"))
    bad = {"B0033", "B0034", "B0036", "B0045", "B0046", "B0047", "B0048", "B0054", "B0055", "B0056"}
    for b in bad:
        row = audit[audit["cell_id"] == b]
        assert not row.empty
        assert row["keep"].iloc[0] == False
