"""Pack monitor, DCR estimator and fault scenarios."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pack.monitor import PackMonitor, fit_iforest  # noqa: E402
from pack.resistance import estimate_dcr  # noqa: E402


def _reading(cycle, soh=95.0, v_end=2.8, r=0.1, t=30.0, **kw):

    d = {
        "cycle": cycle,
        "soh": soh,
        "v_end": v_end,
        "resistance_ohm": r,
        "temp_max": t,
    }
    d.update(kw)
    return d


def _run(monitor, per_cell_rows):

    report = None

    for i in range(len(next(iter(per_cell_rows.values())))):

        for c, rows in per_cell_rows.items():

            monitor.update(c, rows[i])

        report = monitor.evaluate()

    return report


def codes(report):

    return {a["code"] for a in report["alerts"]}


def test_dcr_recovers_known_resistance():

    t = np.arange(0, 20, 1.0)
    i = np.where(t < 10, 0.5, 2.5)
    v = 3.9 - 0.15 * i  # R = 0.15 ohm
    out = estimate_dcr(v, i, t)

    assert np.isclose(out["dcr_ohm"], 0.15)
    assert out["steps"] == 1


def test_dcr_none_without_a_step():

    t = np.arange(10.0)
    assert estimate_dcr(np.full(10, 3.7), np.full(10, 1.0), t) is None


def test_dcr_nasa_sign_convention():

    t = np.arange(0, 20, 1.0)
    i = np.where(t < 10, -0.5, -2.5)
    v = 3.9 + 0.15 * i
    out = estimate_dcr(v, i, t, discharge_positive=False)

    assert np.isclose(out["dcr_ohm"], 0.15)


def test_healthy_pack_is_ok():

    m = PackMonitor(["a", "b", "c"])
    rows = {
        c: [_reading(k, soh=99 - 0.05 * k) for k in range(1, 30)]
        for c in "abc"
    }

    r = _run(m, rows)

    assert r["maintenance"] == "OK"
    assert r["alerts"] == []


def test_imbalance_levels():

    for gap, expected in [(0.04, "watch"), (0.07, "warn"), (0.15, "critical")]:

        m = PackMonitor(["a", "b", "c"])
        rows = {
            "a": [_reading(1, v_end=2.80)],
            "b": [_reading(1, v_end=2.80)],
            "c": [_reading(1, v_end=2.80 - gap)],
        }
        r = _run(m, rows)
        sev = [a["severity"] for a in r["alerts"] if a["code"] == "IMBALANCE"]

        assert sev == [expected]
        assert r["pack"]["weakest_cell"] == "c"


def test_persistent_weak_cell_flag():

    m = PackMonitor(["a", "b", "c"])
    rows = {
        "a": [_reading(k) for k in range(1, 8)],
        "b": [_reading(k) for k in range(1, 8)],
        "c": [_reading(k, v_end=2.74) for k in range(1, 8)],
    }
    r = _run(m, rows)

    assert "WEAK_CELL" in codes(r)


def test_resistance_spike_against_own_baseline():

    m = PackMonitor(["a", "b"])
    base = [_reading(k, r=0.10) for k in range(1, 12)]
    spike = base + [_reading(12, r=0.25), _reading(13, r=0.26)]
    ok = base + [_reading(12, r=0.10), _reading(13, r=0.10)]
    r = _run(m, {"a": spike, "b": ok})
    spiked = [a for a in r["alerts"] if a["code"] == "RESISTANCE_SPIKE"]

    assert [a["cell"] for a in spiked] == ["a"]


def test_single_resistance_outlier_is_not_a_spike():

    m = PackMonitor(["a", "b"])
    base = [_reading(k, r=0.10) for k in range(1, 12)]
    blip = base + [_reading(12, r=0.25), _reading(13, r=0.10)]
    ok = base + [_reading(12, r=0.10), _reading(13, r=0.10)]
    r = _run(m, {"a": blip, "b": ok})

    assert "RESISTANCE_SPIKE" not in codes(r)


def test_thermal_levels_and_rate():

    m = PackMonitor(["a"])
    assert "THERMAL" in codes(_run(m, {"a": [_reading(1, t=60)]}))

    m = PackMonitor(["a"])
    r = _run(m, {"a": [_reading(1, t=72)]})
    assert [a["severity"] for a in r["alerts"] if a["code"] == "THERMAL"] == ["critical"]
    assert r["maintenance"] == "STOP"

    m = PackMonitor(["a"])
    r = _run(m, {"a": [_reading(1, dtdt_c_per_s=1.5)]})
    assert "THERMAL_RATE" in codes(r)


def test_cycles_to_retirement_extrapolates_fade():

    m = PackMonitor(["a"])
    rows = [_reading(k, soh=100 - 0.2 * k) for k in range(1, 31)]
    r = _run(m, {"a": rows})
    left = r["cells"]["a"]["cycles_to_retirement"]
    # soh = 94 now, 14 points above 80 at 0.2/cycle -> about 70
    assert 65 <= left <= 75


def test_retirement_threshold_is_80_percent():

    m = PackMonitor(["a"])
    r = _run(m, {"a": [_reading(1, soh=79.0)]})

    assert r["cells"]["a"]["cycles_to_retirement"] == 0
    assert "RETIRE" in codes(r)


def test_stable_cell_has_no_countdown():

    m = PackMonitor(["a"])
    rows = [_reading(k, soh=95.0) for k in range(1, 30)]
    r = _run(m, {"a": rows})

    assert r["cells"]["a"]["cycles_to_retirement"] is None


def test_isolation_forest_flags_outlier_row():

    rng = np.random.default_rng(1)
    healthy = np.column_stack(
        [
            1 + 0.02 * rng.standard_normal(400),
            5 * rng.standard_normal(400),
            0.3 * rng.standard_normal(400),
            0.5 * rng.standard_normal(400),
        ]
    )
    model = fit_iforest(healthy)

    assert model.predict([[3.0, -200.0, -15.0, 25.0]])[0] == -1
    assert model.predict([[1.0, 0.0, 0.0, 0.0]])[0] == 1


def test_bridge_updates_on_new_discharge_only(tmp_path):

    from pack.bridge import PackBridge
    import sqlite3

    b = PackBridge("P", ["a", "b"], db_path=tmp_path / "live.db")

    def payload(seen, v):
        return {
            "soh": 95.0,
            "cycles_seen": seen,
            "last_cycle": {
                "discharge_index": seen,
                "v_end": v,
                "resistance_ohm": 0.1,
                "temp_max": 30.0,
            },
        }

    assert b.on_soh("a", payload(1, 2.80)) is not None
    # same discharge re-published every second: ignored
    assert b.on_soh("a", payload(1, 2.80)) is None
    assert b.on_soh("zzz", payload(1, 2.80)) is None

    r = b.on_soh("b", payload(1, 2.70))

    assert r["pack"]["weakest_cell"] == "b"
    assert any(a["code"] == "IMBALANCE" for a in r["alerts"])

    conn = sqlite3.connect(tmp_path / "live.db")
    assert conn.execute("SELECT COUNT(*) FROM live_pack_cells").fetchone()[0] == 2
    assert conn.execute("SELECT maintenance FROM live_pack_status").fetchone()[0] == "SERVICE"
    assert conn.execute("SELECT COUNT(*) FROM live_pack_alerts").fetchone()[0] >= 1


def test_trajectory_rul_reads_reference_lifetimes():

    from rul_trajectory import TrajectoryRul

    # two reference cells fading linearly 100 -> 80 over 40 and 80
    # discharges (0.5 and 0.25 points per discharge)
    curves = {
        "x": list(100 - 0.5 * np.arange(60)),
        "y": list(100 - 0.25 * np.arange(120)),
    }
    est = TrajectoryRul(curves)

    # at 90% they had 20 and 40 discharges left -> median 30
    assert est.remaining([90.0] * 10) == 30
    assert est.remaining([79.0] * 10) == 0


def test_monitor_uses_trajectory_estimator():

    from rul_trajectory import TrajectoryRul

    est = TrajectoryRul({"x": list(100 - 0.5 * np.arange(60))})
    m = PackMonitor(["a"], rul=est)
    r = _run(m, {"a": [_reading(k, soh=90.0) for k in range(1, 12)]})

    assert r["cells"]["a"]["cycles_to_retirement"] == 20
