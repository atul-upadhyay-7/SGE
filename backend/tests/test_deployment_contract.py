"""Static safeguards for Grafana wiring and complete-discharge model identity."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_live_dashboard_uses_quoted_devices_numeric_cells_and_real_gaps():
    dashboard = json.loads((ROOT / 'docker/grafana/provisioning/dashboards/bms-live.json').read_text())
    assert dashboard['time'] == {'from': 'now-24h', 'to': 'now'}
    assert dashboard['refresh'] == '5s'
    for panel in dashboard['panels']:
        for target in panel['targets']:
            assert 'IN ($device)' not in target['rawSql']
            assert '${device:sqlstring}' in target['rawSql']
        if panel['id'] in {1, 2, 3, 4, 5, 7}:
            assert "'5s', NULL" in panel['targets'][0]['rawSql']
            assert panel['fieldConfig']['defaults']['custom']['spanNulls'] is False
    assert 'c.v::float' in dashboard['panels'][2]['targets'][0]['rawSql']
    assert dashboard['annotations']['list'][0]['enable'] is True
    assert "threshold AS value" not in json.dumps(dashboard)


def test_authenticated_embedding_is_the_default_and_compose_mounts_it():
    config = (ROOT / 'docker/grafana/grafana.ini').read_text()
    anonymous = config.split('[auth.anonymous]', 1)[1].split('[analytics]', 1)[0]
    assert 'enabled = false' in anonymous
    compose = (ROOT / 'docker-compose.yml').read_text()
    assert './docker/grafana/grafana.ini:/etc/grafana/grafana.ini:ro' in compose
    assert 'BMS_GRAFANA_DASHBOARDS_PATH: /var/lib/grafana/bms-dashboards' in compose
    assert './docker/grafana/provisioning/dashboards:/var/lib/grafana/bms-dashboards:ro' in compose
    assert 'BMS_GRAFANA_PG_USER: bms_ro' in compose
