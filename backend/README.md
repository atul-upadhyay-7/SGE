# BMS Backend

FastAPI service that ingests ESP32/BMS telemetry over MQTT, persists raw
measurements in PostgreSQL, and (from Phase 4) serves State-of-Health
predictions from the existing trained model. Built alongside the repo's
offline ML pipeline, and it reuses the same cycle/feature code in later
phases.

## Status: Phases 1–3 complete

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

Not yet implemented (later phases): cycle aggregation, SoH inference.

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
```

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
