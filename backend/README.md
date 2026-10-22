# BMS Backend

FastAPI service that ingests ESP32/BMS telemetry over MQTT, persists raw
measurements in PostgreSQL, aggregates discharge cycles, serves
State-of-Health predictions from the existing trained partial-window model,
and evaluates alert rules against the stored state. Built alongside the
repo's offline ML pipeline, and it reuses that pipeline's own cycle/feature
code (no private copy) so training and serving cannot drift.

## Status: core implemented; full PRD acceptance still open

The earlier "complete" label referred to code milestones, not a full production
acceptance. See `docs/TODO.md` and `docs/ACCEPTANCE_2026-10-22.md`. The Oct 22
hardening patch is local until reviewed/pushed; do not assume hosted main has it.

The live partial-window model is intentional, not an obsolete copy of the
39-input completed-discharge SOH blend. Their feature contracts and availability
are different. Never swap them without a separate ingestion/feature design.

**Phase 1 — Foundation**

- Application settings (`app/core/config.py`), structured logging.
- Async PostgreSQL engine + ORM models for all five PRD tables
  (`app/db/`), managed by Alembic migrations (`migrations/`).
- MQTT connection manager with reconnection and telemetry subscription
  (`app/mqtt/`).
- Health endpoints under `/api/v1`: `/health`, `/health/database`,
  `/health/mqtt`.

**Phase 2 — Reliable ingestion**

- Device registration and lookup: `POST/GET /api/v1/devices`,
  `GET /api/v1/devices/{device_id}` (FR-01).
- One shared validation path for MQTT and HTTP; rejected payloads are
  quarantined in `telemetry_rejections` with a stable reason (FR-02, FR-04).
- Idempotent persistence: `UNIQUE(device_id, message_id)` +
  `ON CONFLICT DO NOTHING`; a re-used message ID with a different payload is
  flagged `conflicting_duplicate` (FR-02).
- Non-blocking ingestion: validated samples go onto a bounded queue and are
  written in batches by a dedicated writer task, so Postgres latency never
  stalls the MQTT consumer (NFR: Reliability).
- Optional HTTP ingestion: `POST /api/v1/devices/{device_id}/telemetry`
  (identical validation/persistence code path).
- Legacy firmware adapter (single-`voltage` frame → PRD cell contract).
- Simulated ESP32 publisher: `scripts/simulate_publisher.py`.
- Ingestion counters: `GET /api/v1/health/ingestion`.

**Phase 3 — Historical queries**

- `GET /api/v1/devices/{device_id}/telemetry` — time-bounded history with
  keyset pagination. Query params: `from`, `to` (inclusive ISO timestamps,
  assumed UTC when naive), `order` (`asc`/`desc`, default `desc`), `limit`
  (1–1000, default 100), `cursor` (opaque; echo back `next_cursor`).
  Keyset on `(measured_at, id)` keeps pages stable while new samples arrive,
  unlike OFFSET.
- `GET /api/v1/devices/{device_id}/telemetry/latest` — newest sample.
- `GET /api/v1/devices/{device_id}/telemetry/summary` — sample count,
  coverage (`first`/`last_measured_at`), device status/`last_seen_at`, and
  rejections grouped by reason (data-quality overview).
- Unknown device → `404`; bad cursor or `from > to` → `400`.

**Phase 4 — Cycle aggregation and SoH inference**

- `POST /api/v1/devices/{device_id}/cycles/aggregate` — replays the device's
  stored telemetry through the offline streaming cycle tracker
  (`src/streaming/cycle_tracker.py`), upserts the completed discharge cycles,
  and refreshes this device's SoH predictions (FR-06, FR-07). Idempotent:
  cycles are keyed on `(device_id, cycle_number)` and predictions are
  replaced, so re-running never double-counts. The per-sample cell used for a
  multi-cell pack is the *weakest* cell (lowest voltage).
- SoH comes from the partial-window model only (`models/soh_window_all`),
  through `app/ml/estimator.py`. The estimator and trainer share one feature
  definition (`src/features.py`); a cell with fewer than
  `WINDOW_MIN_REFERENCE_DISCHARGES` opening discharges is refused rather than
  guessed (reported as `refused` in the response).
- `GET /api/v1/devices/{device_id}/cycles` — aggregated cycles, ascending.
- `GET /api/v1/devices/{device_id}/soh` — stored predictions, oldest first.
- `GET /api/v1/devices/{device_id}/soh/latest` — newest prediction (`404`
  before the device has been aggregated).
- Model provenance: the served artifact is registered in `model_registry`,
  versioned by the SHA-256 of the `.ubj`, and every prediction records its
  `model_version` and `feature_schema_version`.
- CLI equivalent: `python backend/scripts/aggregate_cycles.py --all`
  (or `--device-id <id>`).
- Unknown device → `404` on every endpoint.

**Phase 5 — Monitoring loop and alerts**

- Background monitor (`app/services/monitor_service.py`, one asyncio task in
  the lifespan): each tick it re-aggregates any device that has gained a
  sample since the last aggregation (`devices.last_aggregated_at`), then
  evaluates the alert rules for every registered device.
- Alert rules (`app/services/alert_service.py`), evaluated against the
  *persisted* state (newest sample, newest SoH prediction, `last_seen_at`):
  - `soh_below_retirement` — latest predicted SoH < `BMS_ALERT_SOH_RETIRE_PCT`
    (default 80.0, matching `scripts/make_alert_rules.py`). Critical.
  - `temperature_out_of_range` — newest sample temperature >
    `BMS_ALERT_TEMPERATURE_MAX_C` (default 63.0242 C, the maximum peak
    temperature in `nasa_ml_dataset_clean.csv`, so "out of range" means
    "outside the model's trained operational range"). Warning.
  - `device_stale` — no sample for `BMS_DEVICE_STALE_AFTER_S` (default 900 s).
    Warning.
  - `cell_imbalance` — the `cell_imbalance_high` quality flag the ingestion
    path already computes. Warning.
- Reconciliation is idempotent: a rule that is already open bumps
  `last_seen_at` instead of stacking a row (enforced by a partial unique
  index on `(device_id, rule_code)` where `status <> 'resolved'`). A cleared
  condition resolves the row; a still-firing *acked* alert stays acked.
- Endpoints: `GET /api/v1/alerts` (filter by `device_id`, `status`,
  `severity`; paginated), `GET /api/v1/alerts/{id}`,
  `POST /api/v1/alerts/{id}/ack`, `GET /api/v1/devices/{id}/alerts`, and
  `POST /api/v1/devices/{id}/alerts/evaluate` (force a pass, mirrors
  `/cycles/aggregate`). Loop health: `GET /api/v1/health/monitor`.
- CLI equivalent: `python backend/scripts/evaluate_alerts.py --all`
  (or `--device-id <id>`).
- The loop is disabled in tests (`BMS_MONITOR_ENABLED=false` in conftest), so
  it never races a per-test event loop.

**Phase 6 — Security hardening**

- Per-device shared secrets. `POST /api/v1/devices` accepts an optional
  write-only `credential` (8–128 chars). It is hashed with SHA-256 before
  storage (`devices.credential_ref`) and never appears in any response, so a
  leaked API payload cannot leak a secret. Devices without a credential stay
  open, which keeps local development and the compose stack unchanged.
- Ingestion authenticates the device. A protected device's telemetry must
  carry the secret: as an `X-Device-Credential` header on the HTTP path, or
  in the payload's optional top-level `credential` field (the MQTT
  convention). The inline copy is stripped before schema validation, so it
  can never be persisted into `additional_data`. A mismatch is quarantined
  with reason `device_credential_mismatch` and answered `401` over HTTP.
  The check runs before the status check, so an unauthenticated caller
  cannot probe whether a device is retired.
- Credential rotation: `PUT /api/v1/devices/{device_id}/credential` with
  `{"credential": "<new>"}`. While a secret is still set the caller must
  present it in `X-Device-Credential`; sending `{"credential": null}` clears
  it and re-opens the device.
- Optional admin key. Setting `BMS_ADMIN_API_KEY` guards device registration
  and credential rotation behind an `X-Admin-Key` header (401 when missing
  or wrong). Unset — the default — those endpoints stay open for local use.
- Comparison uses `hmac.compare_digest`, so a timing probe cannot recover
  the stored digest byte by byte.
- Broker-side security is configuration, not code: run Mosquitto with a TLS
  listener, a `password_file`, and per-device ACLs that map the MQTT
  username to a topic prefix, e.g.

  ```
  listener 8883
  cafile /etc/mosquitto/certs/ca.crt
  certfile /etc/mosquitto/certs/server.crt
  keyfile /etc/mosquitto/certs/server.key
  require_certificate false
  password_file /etc/mosquitto/passwd
  acl_file /etc/mosquitto/acl
  ```

  with an ACL granting each device `write` to `bms/${username}/telemetry`
  only, and the backend a `read` on `bms/+/telemetry`. The backend then
  connects with its own username/password (`BMS_MQTT_USERNAME` /
  `BMS_MQTT_PASSWORD`) rather than anonymously.

**Phase 7 — Remaining useful life (EOL prediction)**

- `GET /api/v1/devices/{device_id}/rul` — estimates how many **discharges**
  remain before retirement (SoH < 80%, the project-wide `EOL_SOH_THRESHOLD`).
- Method is *reference-trajectory matching*, not a regressor: the device's
  recent predicted SoH values are matched against the offline reference
  curves (`models/rul_reference_curves.json`) and the median remaining
  discharges across references is reported, with the p10/p90 spread across
  references as a band. The offline analysis (`src/rul_trajectory.py`)
  documents why a cycle-level RUL regressor cannot beat a constant on four
  cells, and why a fade-rate extrapolation overshoots the knee.
- `status` tells the caller what to expect: `ok` (numbers meaningful),
  `retired` (already at/below threshold → `rul_discharges: 0`),
  `no_predictions` (device has no SoH yet), `no_references` (no reference
  curve reached the threshold). Unknown device → `404`; missing artifact →
  `503`.
- RUL is **derived, not stored**: it is recomputed from the current
  predictions on each request, so re-aggregating a device (or updating the
  reference curves) is reflected immediately. The estimator imports the
  offline `TrajectoryRul` class rather than reimplementing it, and a test
  pins the point estimate to the offline `remaining()`.
- CLI equivalent: `python backend/scripts/estimate_rul.py --all`
  (or `--device-id <id>`).
- Honest limits (carried from the offline module): four reference cells of
  one chemistry and one load; the band is a spread across references, not a
  calibrated predictive interval.

**Phase 8 — Live push and dashboard glue**

- `GET /api/v1/stream` is a Server-Sent Events (SSE) channel. The dashboard's
  `EventSource` opens one long-lived request and receives a small JSON frame
  when telemetry is stored, a discharge cycle completes, an alert opens/changes
  or resolves, a device is registered, or a monitor tick runs. Panels then
  re-read the REST endpoints they care about.
- The events are produced by an in-process bus (`app/services/event_bus.py`)
  that ingestion, the monitor loop, alert reconciliation and registration
  publish to. It is a lossy "something changed" hint, not a durable log: a
  subscriber that misses an event still sees the truth on its next read, which
  is why a broker is deliberately not used.
- A slow subscriber never applies back-pressure: each subscriber has a bounded
  queue (`BMS_STREAM_QUEUE_MAX`) and the oldest event is dropped when it fills
  (counted, not silent). `GET /api/v1/stream/status` reports the subscriber
  count and total published events.
- Filter with `?kinds=telemetry,alert` (kinds: `telemetry`, `cycle`, `alert`,
  `monitor`, `device`). Set `BMS_STREAM_ENABLED=false` to turn the channel off
  in a poll-only deployment.
- SSE rather than WebSockets because the traffic is one-directional and plain
  HTTP: it needs no handshake, no client library, and traverses proxies.

## Frontend & Grafana Integration (change request)

The CR reshapes the read surface around what the React frontend and Grafana
need, adds a versioned anomaly subsystem, and adds report generation. Ingestion
is unchanged: MQTT and `POST /api/v1/devices/{device_id}/telemetry` still work
exactly as in Phases 2–8.

### Read API paths (CR §2)

The device-scoped reads are replaced by paths the frontend consumes directly:

| Purpose | Path |
| --- | --- |
| Latest sample | `GET /api/v1/telemetry/latest/{device_id}` |
| History (keyset paged) | `GET /api/v1/telemetry/history/{device_id}` |
| Data-quality summary | `GET /api/v1/telemetry/summary/{device_id}` |
| Device fleet | `GET /api/v1/devices` · `GET /api/v1/devices/{device_id}` |
| Latest / history SoH | `GET /api/v1/predictions/latest/{device_id}` · `GET /api/v1/predictions/history/{device_id}` |
| Active models | `GET /api/v1/models/active` |

`GET /api/v1/models/active` returns the active `model_registry` rows newest
first, so the frontend can attribute every prediction to a served artifact.

### Anomaly subsystem (CR §3, §4, §6)

- **Detection** (`app/services/anomaly_detection_service.py`) runs after each
  ingestion flush, in its own session and transaction. It reconciles the recent
  window against the rule catalog (`app/services/anomaly_rules.py`,
  `RULES_VERSION = "rules-v1"`) and persists episodes to `anomaly_events`. A
  detector fault is isolated: raw samples are already committed, so detection
  can never drop telemetry (CR §10).
- **Rules** cover per-cell voltage min/max, cell-voltage spread, temperature
  min/max, voltage and current jumps, telemetry gaps and staleness. Each rule's
  severity is `severity_for(ratio)` with warning/critical bands.
- **Thresholds are layered**: every chemistry has a profile
  (`li_ion`, `lifepo4`, `nimh`, `lead_acid`) and a device may override any
  column (`cell_voltage_max_v`, `cell_spread_max_v`, `temperature_max_c`, …).
  `resolve_thresholds(device, settings)` is the single place device overrides
  are combined with the chemistry default, so every rule reads one resolved set.
- **Episodes, not rows**: recurrence extends the open episode for the same
  `(device_id, anomaly_type, affected_cell)`; a `resolve` closes it and a later
  recurrence opens a new row. A partial unique index
  (`uq_anomaly_open_per_key`, `status = 'open'`) guarantees at most one open
  episode per key. Every change writes an `anomaly_actions` row, so the audit
  trail (`GET /api/v1/anomalies/{id}/history`) is complete.
- **Endpoints**: `GET /api/v1/anomalies` (filter `device_id`, `severity`,
  `anomaly_type`, `status`, `from`, `to`), `GET /api/v1/anomalies/{id}`,
  its `/history`, `POST /api/v1/anomalies/{id}/acknowledge`,
  `POST /api/v1/anomalies/{id}/actions`, `POST /api/v1/anomalies/{id}/resolve`.
  Aggregates: `GET /api/v1/anomalies/summary` (by severity/type),
  `GET /api/v1/anomalies/hourly`, `GET /api/v1/anomalies/recurrence`.
- **Status flow**: `open → acknowledged`, `open|acknowledged → investigating`,
  `→ resolved`. Acknowledging a resolved anomaly is `409`; `POST .../actions`
  rejects `action_type = resolve` (`400`). Anomalies are **never created by the
  API** — only the detector opens an episode.
- **Hourly aggregation (CR §6)** counts an event in its `detected_at` hour; a
  **still-open** event also counts in every later hour up to
  `last_detected_at`. Buckets are truncated in **UTC** (the session timezone
  must not shift them) and serialized as naive UTC hours.
- Literal `/summary`, `/hourly` and `/recurrence` paths are declared *before*
  `/anomalies/{anomaly_id}` so the path parameter never captures them.

### Reports (CR §7)

- `POST /api/v1/reports` builds a **synchronous, persisted** snapshot
  (`reports.payload`, `format_version = "report-v2"`). One configurable
  **full** report with include-flags (`sections.telemetry/soh/anomalies/actions`,
  all on by default) and filters (`device_id`, `from`/`to`, `severity`,
  `anomaly_type`). A device-scoped report uses `"scope": "device"`; no
  `device_id` produces a fleet report.
- The snapshot has an `executive` summary, `device`, `data_availability`,
  `model_metadata` (including `anomaly_rules.RULES_VERSION`) and the selected
  sections. SoH comes from stored predictions; anomalies from stored episodes —
  a report never runs a model and never invents a value.
- `GET /api/v1/reports` (filter `device_id`, `report_type`),
  `GET /api/v1/reports/{id}`, and
  `GET /api/v1/reports/{id}/download?format=json|csv`. **CSV is the primary
  table only** (the anomaly list, one header row); JSON is the complete
  multi-section report. Unknown report → `404`; unsupported format → `400`;
  an inverted `from > to` window → `422`. The download sets
  `Content-Disposition: attachment; filename="report-{id}.{ext}"`.
- Formats are **JSON, CSV and PDF** from the stored snapshot.

### Grafana (CR §4.2–4.7)

Grafana is **not a required service**; the repository ships provisioning
artifacts under `docker/grafana/` and the stack runs without them. See
`docker/grafana/README.md` for the read-only role SQL and mount points.

- `docker/grafana/provisioning/dashboards/bms-live.json` — the `bms-live`
  dashboard the frontend embeds by UID: a rolling `now-24h`→`now` window with
  auto-refresh and the seven PRD graphs (pack voltage, current, individual cell
  voltages, temperature, cell-voltage spread, anomaly indicator, anomalies
  overlaid on telemetry) plus a data-freshness stat. All panels are PostgreSQL
  queries; line panels set `spanNulls=false`, so gaps are **not** drawn as
  fabricated lines.
- `docker/grafana/provisioning/datasources/bms-datasource.yml` — a
  `postgres` datasource backed by a **read-only** account
  (`BMS_GRAFANA_PG_USER` / `BMS_GRAFANA_PG_PASSWORD`); no write credentials or
  admin password are committed. The legacy battery-pdm datasources/panels are
  left untouched.
- `docker/grafana/grafana.ini` — embedding enabled (`allow_embedding`,
  `cookie_samesite = none`, `cookie_secure`) with no secrets; the frontend
  bundle never carries Grafana admin or DB credentials.

The frontend points at Grafana via `VITE_GRAFANA_URL` and
`VITE_GRAFANA_DASHBOARD_UID=bms-live` and embeds the dashboard as an
authenticated iframe.

### Security and reliability notes for the CR surface (CR §10)

- A device credential (or its digest) appears in **no** read surface — device,
  telemetry, alert, anomaly or report responses were machine-checked for leaks.
- Diagnoses, corrective actions, SoH and recurrence originate from stored rows
  or the rule catalog (PRD §8); a cause is always a hypothesis
  (`confirmed: false`), never an asserted failed component.
- A report over an empty window returns honest zeros and `null`s rather than
  fabricated numbers.

## Run with Docker (recommended)

Both the backend and its database are part of the root `docker-compose.yml`:

```bash
docker compose up -d postgres mosquitto      # datastore + broker
docker compose up --build bms-api            # migrates on boot, then serves
```

- API: http://localhost:8000/docs
- Postgres is published on host port **5433** (not 5432, to avoid colliding
  with a Postgres already installed on the host). The `bms-api` container
  reaches it internally at `postgres:5432`.
- The image copies the shared ML code (`src/`) and the model artifacts
  (`models/`) to `/app`, matching the paths the estimator resolves at runtime.

## Run locally (host Python)

```bash
cd backend
python -m venv venv && venv/Scripts/activate    # or source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                            # then edit

# with `docker compose up -d postgres` already running:
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

## Smoke test with the simulator

```bash
# 1. register a device (cell_count must match the published cell_voltages)
curl -X POST http://localhost:8000/api/v1/devices \
  -H "Content-Type: application/json" \
  -d '{"device_id":"BMS_001","name":"Bench pack","cell_count":3}'

# 2. publish ~1 Hz telemetry; --chaos injects impossible cells for testing
python backend/scripts/simulate_publisher.py --device-id BMS_001 --cell-count 3

# 3. watch counters: stored / duplicates / rejected / dropped
curl http://localhost:8000/api/v1/health/ingestion

# 4. aggregate the discharges seen so far and read back the SoH
curl -X POST http://localhost:8000/api/v1/devices/BMS_001/cycles/aggregate
curl http://localhost:8000/api/v1/devices/BMS_001/soh/latest
```

The simulator publishes at ~1 Hz and the tracker needs at least a couple of
minutes of a real discharge to close a cycle, so a short smoke run produces
cycles=0 (expected). Use recorded telemetry, or `--device-id` with a longer
run, to see SoH predictions.

On Windows the simulator forces the selector event loop, which paho/aiomqtt
require. Re-running it with the same `--device-id` reuses message IDs,
demonstrating idempotency (duplicates) and conflicting-payload quarantine.

## Migrations

Schema is never changed by hand (PRD §5):

```bash
alembic upgrade head                       # apply
alembic revision --autogenerate -m "..."   # create after editing models
alembic downgrade base                     # tear down (dev only)
```

## Tests

```bash
cd backend
python -m pytest
```

Settings are read at import time, so the test suite disables MQTT, points at
the compose Postgres (`localhost:5433`) and uses `NullPool` so per-test event
loops never share a pooled connection. Database-backed tests skip when no
Postgres is reachable, so the unit suite runs without infrastructure.

```bash
docker compose up -d postgres   # start the datastore for the full suite
python -m pytest
```

## Configuration

Every setting is an environment variable prefixed with `BMS_` and documented
in `.env.example`. Secrets (`.env`) are git-ignored; commit `.env.example`
only.


## Hardening patch: operational checks

- `.github/workflows/verify.yml` provisions PostgreSQL 16, migrates/checks schema,
  then runs backend tests with `BMS_REQUIRE_DB=true`. A missing DB fails CI
  instead of skipping integration tests. This workflow has not run on GitHub
  until the patch is pushed.
- `/health/ingestion`: `stored_per_second` is committed inserts over the last
  60 seconds (or since startup), scoped to this process. `persisted_invalid_count`
  is the all-device quarantine-row total, across restarts. During DB outages it
  is null with `invalid_count_status=database_unavailable`, never invented zero.
- Ingestion stored/rejected/duplicate counters advance only after commit.
- Reports use `report-v3` for fleet/availability semantics (v2 corrected filters/sections). Old snapshots
  remain untouched. Exports include every selected row up to 10,000; larger
  synchronous snapshots return 422 asking for narrower filters, not silent
  truncation. A queued high-volume report worker remains open.
- Fleet snapshots now include sample-weighted telemetry statistics, per-device
  SoH history and the mean latest SoH per device in the selected period.
  Internal gaps are partitioned by device; leading/trailing absence is not inferred.
  Rejection totals use receipt time within the requested period. Old snapshots
  remain unchanged. PDF rendering runs in a worker thread, not the API event loop.
- JSON, CSV and PDF are supported from the same stored snapshot. PDF uses
  paginated text with all selected sections and no additional live data reads.
- The all-cell blend has no UBJ export. Its stale XGBoost-only UBJ/JSON files
  were removed and training now cleans them up. Window and demo exports remain.
