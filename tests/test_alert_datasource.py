"""
test_alert_datasource.py
------------------------
An alert rule is only an alert if Grafana can evaluate it. The
JSON-datasource plugin (simpod-json-datasource) declares
"alerting": false and "backend": false in its plugin.json: it is a
frontend-only plugin, so Grafana's alerting engine cannot query it
and every rule that uses it sits at [plugin.unavailable].

This test reads every datasource a rule queries, looks up that
datasource's plugin capability, and fails if the plugin cannot
alert. It is the guard that stops the alert path from silently
regressing back to the JSON plugin.

Plugin capabilities are read from the installed plugin.json when it
is available (GF_PATHS_PLUGINS, /var/lib/grafana/plugins, ...); the
built-in Grafana datasources have no plugin.json on disk and are
listed as alerting-capable. The committed fallback covers a machine
where the plugin is not installed, so the guard still holds.
"""

import json
import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

RULE_FILES = [
    REPO / "docker" / "grafana" / "provisioning" / "alerting" / "alert_rules.yml",
    REPO / "dashboard" / "provisioning" / "alerting" / "alert_rules.yml",
]

DATASOURCE_FILES = [
    REPO / "docker" / "grafana" / "provisioning" / "datasources" / "datasource.yml",
    REPO / "dashboard" / "provisioning" / "datasources" / "grafana_datasource.yml",
]

# Datasources Grafana ships and that support unified alerting.
BUILTIN_ALERTING = {
    "prometheus",
    "loki",
    "tempo",
    "influxdb",
    "elasticsearch",
    "opensearch",
    "cloudwatch",
    "azuremonitor",
    "stackdriver",
    "googlecloudmonitoring",
    "graphite",
    "mysql",
    "postgres",
    "mssql",
    "sql",
    "parquet",
}

# Used only when the plugin is not installed on the machine running
# the tests, so the guard is not silently skipped.
FALLBACK_PLUGIN_FLAGS = {
    "simpod-json-datasource": {"alerting": False, "backend": False},
}


def plugin_dirs():
    candidates = []

    env = os.environ.get("GF_PATHS_PLUGINS")

    if env:

        candidates.append(Path(env))

    candidates += [
        Path("/var/lib/grafana/plugins"),
        Path("/usr/share/grafana/data/plugins"),
    ]

    return [path for path in candidates if path.is_dir()]


def find_manifest(datasource_type):
    """Return the plugin.json dict for a datasource type, or None."""

    for base in plugin_dirs():

        direct = base / datasource_type / "plugin.json"

        candidates = [direct] if direct.is_file() else []
        candidates += list(base.glob("*/plugin.json"))

        for path in candidates:

            try:

                data = json.loads(path.read_text())

            except (OSError, ValueError):

                continue

            if data.get("id") == datasource_type:

                return data

    return None


def plugin_flags(datasource_type):
    """Alerting/backend capability of a datasource type."""

    if datasource_type in BUILTIN_ALERTING:

        return {"alerting": True, "backend": True}

    manifest = find_manifest(datasource_type)

    if manifest is not None:

        return {
            "alerting": bool(manifest.get("alerting", False)),
            "backend": bool(manifest.get("backend", False)),
        }

    return FALLBACK_PLUGIN_FLAGS.get(datasource_type, {})


def parse_datasources(text):
    """Map datasource uid -> type from a provisioning YAML file."""

    mapping = {}
    uid = None

    for line in text.splitlines():

        stripped = line.strip()

        if stripped.startswith("uid:"):

            uid = stripped.split(":", 1)[1].strip()

        elif stripped.startswith("type:") and uid:

            mapping[uid] = stripped.split(":", 1)[1].strip()
            uid = None

    return mapping


def rule_datasource_uids(text):
    """Datasource uids a rule file queries (excluding __expr__)."""

    return {
        uid
        for uid in re.findall(r"datasourceUid: (\S+)", text)
        if uid != "__expr__"
    }


def provisioned_types():
    mapping = {}

    for path in DATASOURCE_FILES:

        for uid, dtype in parse_datasources(path.read_text()).items():

            mapping.setdefault(uid, dtype)
            assert mapping[uid] == dtype, (
                f"{uid} is {mapping[uid]!r} in one tree and {dtype!r} "
                "in the other"
            )

    return mapping


def test_the_json_plugin_really_cannot_alert():
    """Documents the root cause this whole path exists to avoid."""

    flags = plugin_flags("simpod-json-datasource")

    assert flags.get("alerting") is False, flags
    assert flags.get("backend") is False, flags


def test_every_rule_datasource_can_alert():
    types = provisioned_types()
    seen = set()

    for path in RULE_FILES:

        for uid in rule_datasource_uids(path.read_text()):

            seen.add(uid)

            dtype = types.get(uid)

            assert dtype, f"{path.name}: {uid} is not a provisioned datasource"

            flags = plugin_flags(dtype)

            assert flags.get("alerting") is not False, (
                f"{path.name}: rule datasource {uid!r} has type {dtype!r}, "
                f"whose plugin declares alerting:false — Grafana cannot "
                f"evaluate the rule"
            )

            assert flags.get("backend") is not False, (
                f"{path.name}: rule datasource {uid!r} ({dtype!r}) is not a "
                "backend datasource, so Grafana alerting cannot query it"
            )

    assert "battery-pdm-prom" in seen, (
        "no rule queries the Prometheus datasource"
    )
    assert "battery-pdm-ds" not in seen, (
        "a rule still queries the JSON-datasource plugin"
    )


def test_the_guard_would_reject_the_json_datasource():
    """The guard must actually fail on the old datasource."""

    assert plugin_flags("simpod-json-datasource").get("alerting") is False
