"""
test_check_alerts.py
--------------------
check_alerts.py reproduces the provisioned Grafana rule logic,
because the JSON datasource plugin declares "alerting": false and
Grafana's own engine cannot evaluate the rules. These tests pin the
logic: reduce(last), the lt/gt comparison, and the firing verdict
for a series above and below the bound.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

sys.path.insert(0, str(SCRIPTS))

import check_alerts as ca  # noqa: E402
import make_alert_rules as mar  # noqa: E402


def test_reduce_last_returns_the_final_value():
    assert ca.reduce_last([1.0, 2.0, 3.5]) == 3.5


def test_reduce_last_is_none_for_an_empty_series():
    assert ca.reduce_last([]) is None


def test_reduce_last_skips_none_values():
    assert ca.reduce_last([1.0, None, 2.0]) == 2.0


def test_lt_fires_below_and_not_above():
    assert ca.evaluate(79.0, 80.0, "lt") is True
    assert ca.evaluate(80.0, 80.0, "lt") is False
    assert ca.evaluate(81.0, 80.0, "lt") is False


def test_gt_fires_above_and_not_below():
    assert ca.evaluate(64.0, 63.0242, "gt") is True
    assert ca.evaluate(63.0242, 63.0242, "gt") is False
    assert ca.evaluate(50.0, 63.0242, "gt") is False


def test_none_never_fires():
    assert ca.evaluate(None, 80.0, "lt") is False


def test_unknown_direction_is_an_error():
    try:
        ca.evaluate(1.0, 2.0, "sideways")
    except ValueError:
        return
    raise AssertionError("expected ValueError for an unknown direction")


def test_check_reports_firing_for_an_over_threshold_series(monkeypatch):
    temp = mar.trained_peak_temperature_c()
    rules = mar.build_rules(temp)

    series = {
        "live_soh_by_cycle": [90.0, 85.0, 70.0],       # below 80 -> fires
        "live_temperature_by_cycle": [30.0, 40.0, 41.0],  # below bound -> normal
    }

    monkeypatch.setattr(
        ca, "fetch_series",
        lambda target, base=ca.DEFAULT_BASE, timeout=5.0: series[target],
    )

    results = {rule["uid"]: firing for rule, _, firing in ca.check(rules)}

    assert results["alert_soh_below_threshold"] is True
    assert results["alert_input_out_of_range"] is False


def test_require_firing_exits_nonzero_when_nothing_fires(monkeypatch):
    rules = mar.build_rules(mar.trained_peak_temperature_c())

    series = {
        "live_soh_by_cycle": [95.0, 96.0, 97.0],        # above 80 -> normal
        "live_temperature_by_cycle": [30.0, 40.0, 41.0],  # below bound -> normal
    }

    monkeypatch.setattr(
        ca, "fetch_series",
        lambda target, base=ca.DEFAULT_BASE, timeout=5.0: series[target],
    )

    assert ca.main(["--require-firing"]) == 1
