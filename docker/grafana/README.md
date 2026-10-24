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


## Compose setup (hardening patch)

Compose now passes the datasource variables, mounts `grafana.ini`, and mounts
BMS dashboards at a distinct path from the legacy dashboards. PostgreSQL is a
startup dependency. Only BMS PostgreSQL is the default datasource.

Set a dedicated `BMS_GRAFANA_PG_PASSWORD` in your local environment or secrets
manager (compose defaults to `bms_ro`). After migrations, `bms-api` runs the
provisioning script automatically on startup, or you can provision it directly:

```
python -m scripts.setup_grafana_reader
```

For compose, `bms-api` configures the reader on boot and Grafana connects
automatically:

```
docker compose up -d
```

The script grants SELECT on existing tables and default SELECT privileges on
future tables created by the migration owner. It rotates the dedicated role's
password when rerun. It is for a new dedicated reader, not a pre-existing
privileged account. Verify INSERT is denied after deployment.

Anonymous viewing is disabled. Sign in to Grafana in a separate tab before
opening the app. Compose's defaults are for same-site local HTTP. For cross-site
production HTTPS set `GRAFANA_COOKIE_SAMESITE=none` and
`GRAFANA_COOKIE_SECURE=true`; a same-site reverse proxy may still be needed for
third-party-cookie restrictions. No service tokens belong in the frontend.

Telemetry line panels aggregate into 5-second buckets with explicit NULL fill,
not zero fill. The 1Hz source remains stored unchanged. `spanNulls=false` then
shows missing buckets as gaps. Device values use `${device:sqlstring}` and cell
JSON values are cast to numeric. Anomaly annotations carry event times instead
of incorrectly plotting temperature/current thresholds against a voltage axis.

Verified natively with Grafana 11.2.0 + PostgreSQL 14 on Oct 22. Docker compose
itself, PostgreSQL 16 CI and the target deployment remain to be exercised.
