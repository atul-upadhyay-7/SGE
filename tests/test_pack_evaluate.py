

def test_load_df_filters_before_checking_predictions(tmp_path, monkeypatch):
    import pandas as pd
    from pack import evaluate

    proc = tmp_path / "data" / "processed"
    proc.mkdir(parents=True)
    # 9 cells total in clean dataset
    rows = []
    for cid in ["B0001", "B0002", "B0003", "B0004", "B0005", "B0006", "B0007", "B0008", "B0009", "B0018"]:
        for cy in [1, 2]:
            rows.append({"cell_id": cid, "cycle": cy, "capacity_ah": 1.0})
    df_clean = pd.DataFrame(rows)
    df_clean.to_csv(proc / "nasa_ml_dataset_clean.csv", index=False)
    # only 4 have predictions (all cycles)
    for cid in ["B0005", "B0006", "B0007", "B0018"]:
        pd.DataFrame({"cell_id": [cid] * 2, "cycle": [1, 2], "predicted_soh": [90.0, 89.0]}).to_csv(
            proc / f"soh_predictions_{cid}.csv", index=False
        )
    monkeypatch.setattr(evaluate, "PROC", proc)
    # with the 4 that have predictions it must succeed
    out = evaluate.load_df(cells=["B0005", "B0006", "B0007", "B0018"])
    assert set(out["cell_id"].unique()) == {"B0005", "B0006", "B0007", "B0018"}
    # cell with partial predictions must raise
    pd.DataFrame({"cell_id": ["B0002"], "cycle": [1], "predicted_soh": [90.0]}).to_csv(
        proc / "soh_predictions_B0002.csv", index=False
    )
    import pytest

    with pytest.raises(RuntimeError):
        evaluate.load_df(cells=["B0002"])
