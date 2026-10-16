"""
test_alert_rules.py
-------------------
The Grafana alert rules are generated from the training data by
scripts/make_alert_rules.py. These tests hold the properties that
matter:

  * structure — every rule is query -> reduce(last) -> threshold,
    and the condition is the last node, so Grafana can reduce a
    multi-point series to the value the threshold compares;
  * datasource — the query goes to the Prometheus datasource, not
    the JSON-datasource plugin, whose plugin.json declares
    "alerting": false and which Grafana therefore cannot evaluate;
  * failure states — noDataState and execErrState are Alerting, so a
    dead or erroring datasource is reported instead of hidden;
  * data — the temperature bound is the maximum peak temperature
    in the clean training set, not a hand-typed constant.

The files are read as text; PyYAML is not a project dependency.
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

sys.path.insert(0, str(SCRIPTS))

import make_alert_rules as mar  # noqa: E402

RULE_FILES = [
    REPO / "docker" / "grafana" / "provisioning" / "alerting" / "alert_rules.yml",
    REPO / "dashboard" / "provisioning" / "alerting" / "alert_rules.yml",
]


def read(path):
    return path.read_text()


def rule_blocks(text):
    parts = text.split("\n      - uid: ")[1:]
    return ["- uid: " + part for part in parts]


def test_both_provisioning_copies_exist_and_match():
    texts = [read(path) for path in RULE_FILES]
    assert texts[0] == texts[1], "docker and native rule files diverged"


def test_files_match_the_generator_output():
    """A hand-edited rule file fails here, like a hand-edited number."""

    temp = mar.trained_peak_temperature_c()
    expected = mar.render(temp)
    for path in RULE_FILES:
        assert read(path) == expected, f"{path} differs from make_alert_rules"


def test_every_rule_is_query_reduce_threshold():
    for path in RULE_FILES:
        blocks = rule_blocks(read(path))
        assert blocks, f"no rules found in {path}"
        for block in blocks:
            ref_ids = re.findall(r"refId: ([A-Z])", block)
            assert ref_ids == ["A", "B", "C"], ref_ids
            assert "condition: C" in block
            assert "type: reduce" in block
            assert "reducer: last" in block
            assert "expression: A" in block
            assert "type: threshold" in block
            assert "expression: B" in block


def test_query_uses_the_prometheus_datasource():
    """The alerting datasource must be a backend datasource."""

    for path in RULE_FILES:
        text = read(path)
        assert f"datasourceUid: {mar.ALERT_DATASOURCE_UID}" in text
        assert "battery-pdm-prom" in text
        # The frontend-only JSON plugin cannot back alerting; it must
        # not appear as a rule datasource.
        assert "datasourceUid: battery-pdm-ds" not in text


def test_no_data_and_exec_error_states_are_alerting():
    for path in RULE_FILES:
        for block in rule_blocks(read(path)):
            assert "noDataState: Alerting" in block
            assert "execErrState: Alerting" in block
            assert "noDataState: OK" not in block
            assert "execErrState: OK" not in block


def test_condition_is_the_last_data_node():
    """The threshold must be last; the old rules had it second."""

    for path in RULE_FILES:
        for block in rule_blocks(read(path)):
            ref_ids = re.findall(r"refId: ([A-Z])", block)
            assert ref_ids[-1] == "C"


def test_window_is_recent_not_twenty_four_hours():
    for path in RULE_FILES:
        text = read(path)
        assert f"from: {mar.WINDOW_SECONDS}" in text
        assert "from: 86400" not in text


def test_rules_query_the_exporter_metrics():
    temp = mar.trained_peak_temperature_c()
    rules = {r["uid"]: r for r in mar.build_rules(temp)}

    assert rules["alert_soh_below_threshold"]["expr"] == mar.SOH_METRIC
    assert rules["alert_input_out_of_range"]["expr"] == mar.TEMP_METRIC

    text = read(RULE_FILES[0])
    assert f"expr: {mar.SOH_METRIC}" in text
    assert f"expr: {mar.TEMP_METRIC}" in text


def test_temperature_bound_is_the_training_maximum():
    temp = mar.trained_peak_temperature_c()
    rules = {r["uid"]: r for r in mar.build_rules(temp)}
    temp_rule = rules["alert_input_out_of_range"]
    assert temp_rule["direction"] == "gt"
    assert temp_rule["threshold"] == temp
    assert f"params: [{temp:.4f}]" in read(RULE_FILES[0])


def test_soh_bound_is_the_retirement_threshold():
    temp = mar.trained_peak_temperature_c()
    soh_rule = {
        r["uid"]: r for r in mar.build_rules(temp)
    }["alert_soh_below_threshold"]
    assert soh_rule["direction"] == "lt"
    assert soh_rule["threshold"] == 80.0
