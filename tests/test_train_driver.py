"""Training workflow regressions: no slow fitting needed."""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("train_driver", ROOT / "train.py")
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


def test_pipeline_has_dependent_stages():
    spec = importlib.util.spec_from_file_location("pipeline", ROOT / "run_pipeline.py")
    pipeline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pipeline)
    names = [name for name, _ in pipeline.STAGES]
    assert names.index("train_soh") < names.index("rul_trajectory")
    assert names.index("rul_trajectory") < names.index("pack_evaluate")
    assert names.index("pack_evaluate") < names.index("load_to_sqlite")


def test_optimistic_fold_does_not_fit_held_out_rows(monkeypatch):
    import train_soh
    df = pd.DataFrame({"cell_id": ["A", "A", "B", "B"],
                       "x": [1, 2, 3, 4], "soh": [100, 90, 95, 85]})
    seen = []
    class Model:
        def fit(self, x, y):
            self.indices = set(x.index)
        def predict(self, x):
            assert not self.indices.intersection(x.index)
            seen.append(self.indices)
            return np.full(len(x), 90.)
    monkeypatch.setattr(train_soh, "search_best_params", lambda *a, **k: ({}, -1))
    monkeypatch.setattr(train_soh, "build_model", lambda *a, **k: Model())
    assert len(train_soh.optimistic_loco(df, ["x"])) == 2
    assert seen == [{2, 3}, {0, 1}]


@pytest.mark.parametrize("n", ["0", "-1"])
def test_nonpositive_iterations_rejected(n):
    with pytest.raises(SystemExit) as error:
        driver.main(["--n-iter", n])
    assert error.value.code == 2


def test_preflight_failure_trains_nothing(monkeypatch):
    monkeypatch.setattr(driver, "preflight", lambda: (False, ["FAIL test"]))
    monkeypatch.setattr(driver, "run_stage", lambda *a: pytest.fail("ran stage"))
    assert driver.main([]) == 2


def test_check_does_not_train(monkeypatch):
    monkeypatch.setattr(driver, "preflight", lambda: (True, ["ok"]))
    monkeypatch.setattr(driver, "run_stage", lambda *a: pytest.fail("ran stage"))
    assert driver.main(["--check"]) == 0


def test_unknown_stage_rejected(monkeypatch):
    monkeypatch.setattr(driver, "preflight", lambda: (True, ["ok"]))
    assert driver.main(["--only", "nonexistent"]) == 2


def test_partial_run_never_claims_full_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(driver, "ROOT", tmp_path)
    monkeypatch.setattr(driver, "preflight", lambda: (True, ["ok"]))
    monkeypatch.setattr(driver, "run_stage", lambda name, *a: {
        "stage": name, "ok": True, "seconds": 0, "log": "test.log"})
    monkeypatch.setattr(driver, "gates", lambda mode: [("fake", True, "ok")])
    assert driver.main(["--only", "load_data", "--no-backup"]) == 1


def test_failed_stage_stops_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(driver, "ROOT", tmp_path)
    monkeypatch.setattr(driver, "preflight", lambda: (True, ["ok"]))
    calls = []
    def fail(name, *args):
        calls.append(name)
        return {"stage": name, "ok": False, "seconds": 0, "log": "failed.log"}
    monkeypatch.setattr(driver, "run_stage", fail)
    assert driver.main(["--no-backup"]) == 1
    assert calls == ["load_data"]


def test_backup_keeps_original_files(tmp_path, monkeypatch):
    models = tmp_path / "models"
    proc = tmp_path / "data" / "processed"
    models.mkdir()
    proc.mkdir(parents=True)
    (models / "model.txt").write_text("original")
    monkeypatch.setattr(driver, "ROOT", tmp_path)
    monkeypatch.setattr(driver, "MODELS", models)
    monkeypatch.setattr(driver, "PROC", proc)
    dest = driver.backup("test")
    assert (dest / "models" / "model.txt").read_text() == "original"
    assert (models / "model.txt").read_text() == "original"
