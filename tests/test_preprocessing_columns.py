from pathlib import Path

import pandas as pd

EXPECTED = ['group', 'nominal_current_a', 'nominal_temperature_c']


def test_preprocessing_adds_nominal_columns():
    df = pd.read_csv(Path("data/processed/nasa_ml_dataset.csv"))
    for col in EXPECTED:
        assert col in df.columns, f"missing {col}"
    # all non-null for cells that appear
    assert df[EXPECTED].notna().any().all()
