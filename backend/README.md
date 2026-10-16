# BMS Backend

FastAPI service that ingests ESP32/BMS telemetry over MQTT, persists raw
measurements in PostgreSQL, and (from Phase 4) serves State-of-Health
predictions from the existing trained model. Built alongside the repo's
offline ML pipeline, and it reuses the same cycle/feature code in later
phases.

## Status: Phase 1 — Foundation

Implemented:

- Application settings (`app/core/config.py`), structured logging.
- Async PostgreSQL engine + ORM models for all five PRD tables
  (`app/db/`), managed by Alembic migrations (`migrations/`).
- MQTT connection manager with reconnection and telemetry subscription
  (`app/mqtt/`) — connectivity only; persistence lands in Phase 2.
- Health endpoints under `/api/v1`: `/health`, `/health/database`,
  `/health/mqtt`.

Not yet implemented (later phases): device registration, telemetry
persistence, historical APIs, cycle aggregation, SoH inference.

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

Settings are read at import time, so the test suite disables MQTT and points
at the compose Postgres (`localhost:5433`). The database test skips when no
Postgres is reachable, so the rest of the suite runs without infrastructure.

## Configuration

Every setting is an environment variable prefixed with `BMS_` and documented
in `.env.example`. Secrets (`.env`) are git-ignored; commit `.env.example`
only.
