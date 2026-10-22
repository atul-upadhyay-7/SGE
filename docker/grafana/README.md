# Grafana provisioning (BMS backend)

Configuration artifacts only. Grafana is **not** required to run the backend,
and nothing here is code. They exist so an operator can stand up a Grafana that
shows the rolling 24-hour live view described in `frontend/prd.md` §4.2 without
hand-clicking datasources or panels.

## What is provisioned

| File | Purpose |
| --- | --- |
| `provisioning/datasources/bms-datasource.yml` | PostgreSQL datasource (`uid: bms-postgres`) using a **read-only** role, plus an optional Prometheus datasource (`uid: bms-prometheus`). |
| `provisioning/dashboards/bms-dashboards.yml` | File provider for the CR dashboard set. |
| `provisioning/dashboards/bms-live.json` | Dashboard `uid: bms-live` — the 7 PRD §4.2 graphs, `now-24h` window, `5s` refresh, per-device `$device` variable. |
| `grafana.ini` | Reference config enabling authenticated iframe embedding (no secrets). |

The legacy research stack keeps its own files under
`provisioning/datasources/datasource.yml` and `dashboard/provisioning/`; the CR
set above is additive and independent.

## Read-only database role

The datasource must not be able to write. Create a dedicated role once:

```sql
CREATE ROLE bms_ro LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE bms TO bms_ro;
GRANT USAGE ON SCHEMA public TO bms_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO bms_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO bms_ro;
```

## Environment

Export before starting Grafana (defaults suit the compose network):

```
BMS_POSTGRES_HOST=postgres        BMS_POSTGRES_PORT=5432
BMS_POSTGRES_DB=bms               BMS_GRAFANA_PG_USER=bms_ro
BMS_GRAFANA_PG_PASSWORD=...       BMS_PROMETHEUS_URL=http://prometheus:9090
BMS_GRAFANA_DASHBOARDS_PATH=/var/lib/grafana/dashboards
```

## Mounts

```
/etc/grafana/provisioning  <-  docker/grafana/provisioning
/etc/grafana/grafana.ini   <-  docker/grafana/grafana.ini
/var/lib/grafana/dashboards <- docker/grafana/provisioning/dashboards
```

## Frontend embedding

The frontend embeds `$VITE_GRAFANA_URL/d/bms-live` on the Live Monitoring page
(`VITE_GRAFANA_DASHBOARD_UID=bms-live`). Embedding needs `allow_embedding = true`
and a cookie policy that survives the iframe (see `grafana.ini`). Only public,
non-secret values reach the bundle.

## Data gaps, not fabricated lines

Every panel leaves `spanNulls = false`, so a gap in telemetry renders as a gap
rather than a continuous line (PRD §4.2). Anomaly panels read `anomaly_events`,
so the anomaly indicator and overlays are the detector's own persisted events.
