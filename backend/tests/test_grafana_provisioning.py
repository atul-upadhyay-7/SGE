"""Stage 9: the Grafana provisioning is config-only and CR-aligned.

These artifacts ship with the repository but Grafana is not a required service,
so the tests here are file-level invariants: the dashboard exists and is valid
JSON with the UID and window the frontend embeds, the datasource uses a
read-only account and carries no secret, and the embedding config enables iframe
access without committing credentials. They need no database and no Grafana.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
GRAFANA = REPO_ROOT / "docker" / "grafana"

DASHBOARD = GRAFANA / "provisioning" / "dashboards" / "bms-live.json"
DASHBOARD_PROVIDER = (
    GRAFANA / "provisioning" / "dashboards" / "bms-dashboards.yml"
)
DATASOURCE = GRAFANA / "provisioning" / "datasources" / "bms-datasource.yml"
GRAFANA_INI = GRAFANA / "grafana.ini"


def test_dashboard_file_exists_and_is_valid_json() -> None:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    assert dashboard["uid"] == "bms-live"
    assert dashboard["title"]


def test_dashboard_uses_a_rolling_24h_window() -> None:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    assert dashboard["time"] == {"from": "now-24h", "to": "now"}
    assert dashboard["refresh"]


def test_dashboard_covers_every_prd_graph() -> None:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    titles = " | ".join(panel["title"].lower() for panel in dashboard["panels"])
    for expected in (
        "pack voltage",
        "current",
        "cell voltages",
        "temperature",
        "spread",
        "anomaly indicator",
        "overlay",
    ):
        assert expected in titles, expected


def test_dashboard_panels_read_the_postgres_datasource() -> None:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    for panel in dashboard["panels"]:
        assert panel["datasource"]["uid"] == "bms-postgres", panel["title"]
        for target in panel["targets"]:
            assert target["rawQuery"] is True
            # The rolling-window panels are time-filtered; the freshness stat
            # deliberately reads "now" and so has no time filter.
            if panel["type"] == "timeseries":
                assert "$__timeFilter" in target["rawSql"], panel["title"]


def test_gaps_are_not_fabricated_as_lines() -> None:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    line_panels = [
        panel
        for panel in dashboard["panels"]
        if panel["fieldConfig"]["defaults"].get("custom", {}).get("drawStyle")
        == "line"
    ]
    assert line_panels
    for panel in line_panels:
        assert panel["fieldConfig"]["defaults"]["custom"]["spanNulls"] is False


def test_datasource_is_valid_yaml_with_a_read_only_role() -> None:
    parsed = yaml.safe_load(DATASOURCE.read_text(encoding="utf-8"))
    primary = parsed["datasources"][0]
    assert primary["uid"] == "bms-postgres"
    assert primary["type"] == "postgres"
    # The connection user is the read-only role, supplied by the environment.
    assert primary["user"] == "${BMS_GRAFANA_PG_USER}"


def test_datasource_commits_no_password() -> None:
    text = DATASOURCE.read_text(encoding="utf-8")
    # The only password reference is an interpolation of an env var.
    assert "${BMS_GRAFANA_PG_PASSWORD}" in text
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "password" in line.lower() and "${" not in line:
            raise AssertionError(f"literal password in {line!r}")


def test_dashboard_provider_is_valid_yaml() -> None:
    parsed = yaml.safe_load(DASHBOARD_PROVIDER.read_text(encoding="utf-8"))
    provider = parsed["providers"][0]
    assert provider["type"] == "file"
    assert "$__env{BMS_GRAFANA_DASHBOARDS_PATH}" in provider["options"]["path"]


def test_embedding_is_authenticated_and_secret_free() -> None:
    text = GRAFANA_INI.read_text(encoding="utf-8")
    assert "allow_embedding = true" in text
    assert "cookie_samesite" in text
    # No admin password or service token is committed.
    lowered = text.lower()
    assert "gf_security_admin_password" not in lowered
    assert "token =" not in lowered
