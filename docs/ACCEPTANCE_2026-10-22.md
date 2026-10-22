# Local software acceptance: 2026-10-22

Reviewed base: `a30b9ba487ef6f128b3bafc9b8e7df0d9f3a8caa`.
Local hardening patch only. No push or deployment claimed.

## Environment and method

Python 3.12.15 with pinned backend/root dependencies; Node 22; Chrome 142;
PostgreSQL 14 native throwaway database; Grafana OSS 11.2.0 native process;
FastAPI and React/Vite. MQTT disabled in this browser test. NASA archive
checksum verified with `scripts/download_dataset.sh`; legacy SQLite generated
with `dashboard/load_to_sqlite.py`. Run backend and root pytest separately:
their conftest/import namespaces conflict when combined.

`ACCEPTANCE_ONLY` is an explicitly named software fixture persisted through the
real HTTP ingestion path. It has a deliberately missing interval. It is neither
an ESP32 measurement nor an ML accuracy dataset. No mock API replaced backend
responses during the core route/Grafana tests.

## Confirmed checks

- Real PostgreSQL backend suite: baseline 197 pass; local hardening: **207 pass, zero DB skips**, with tests
  for committed counters/rates, persisted invalid total, report filters,
  section toggles, complete >1,000-row history, mixed date offsets and limits.
- Alembic on an empty database: upgrade to head and schema check succeed.
- Root suite after NASA/SQLite preparation: 330 pass, one legacy raw-path skip.
- Frontend five regression tests (all pass, including Asia/Kolkata TZ): StrictMode immediate load, no request overlap,
  old-device cancellation, naive UTC parsing and local minute preservation.
- Frontend typecheck and production build succeed. The seven primary routes
  were also rerun against `vite preview` serving the built production bundle
  against the real FastAPI backend, with no uncaught browser exceptions.
  Expected missing-prediction 404 responses are not treated as failed inference.
- Seven primary routes render on desktop (1440x1000) and mobile (390x844), with
  no document-level horizontal overflow after fixes; anomaly details is eighth.
- Authenticated Grafana embeds on desktop/mobile. Mobile panels are virtualized
  until the iframe scrolls into view. Login was through the separate Grafana tab.
- All eight live dashboard SQL panels execute through Grafana's actual datasource.
  Cell voltage values are numeric. Time-series frames contain explicit NULL
  missing buckets, not zeros. A focused 15-minute visual confirms broken lines
  across the deliberately missing interval; normal embed remains 24 hours.
- Read-only DB role: SELECT succeeds, INSERT denied.
- New HTTP sample returns 201; SSE event reaches React; Reporting/fresh timestamp
  updates without navigation or reload. Default embed uses now-24h/now, 5s.
- Browser anomaly investigation -> acknowledge -> corrective action -> two-step
  resolve: final persisted state is resolved, notes remain in the audit trail.
- Browser report generation -> stored report -> JSON download succeeds.
- Expected missing-prediction 404 is presented as no prediction, not a fake SoH.

## Not established by this check

No real hardware, live broker, Docker daemon/image build, GitHub workflow run,
production HTTPS/cookies/proxy, real viewer-role SSO, sustained 24-hour wall-clock
rollover, or scale/load behavior was validated. Relative bounds and SQL were
checked; a wall-clock day was not elapsed. CSV has backend tests; PDF not built.
Not every PRD field/workflow is implemented. See `TODO.md` for exact open items.
Model-registry display is not a live inference health probe.

## Reproduce locally

```
# Use a throwaway PostgreSQL, not production: backend tests reset tables.
export BMS_DATABASE_URL=postgresql+asyncpg://bms:YOUR_LOCAL_PASSWORD@127.0.0.1:5433/bms
export BMS_REQUIRE_DB=true
cd backend
python -m alembic upgrade head
python -m alembic check
python -m pytest -ra
cd ..
bash scripts/download_dataset.sh
python dashboard/load_to_sqlite.py
python -m pytest -ra
cd frontend
npm ci
npm test
npm run typecheck
npm run build
```

Deployment secrets are private environment configuration. Never put them in
VITE_* variables, committed files, screenshots or shared browser test fixtures.
