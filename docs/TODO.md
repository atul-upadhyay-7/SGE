# Remaining work: verified against code, 2026-10-22

Base reviewed: `a30b9ba487ef6f128b3bafc9b8e7df0d9f3a8caa`.
The previous document incorrectly said the whole tree was uncommitted. Amaanali's
Oct 22 commit published that work. The hardening work below is a local patch until
reviewed and pushed. Do not interpret code milestones as production acceptance.

## Implemented in the local hardening patch

- [x] Frontend public `.env.example` exists; default Grafana port matches compose
  (3050). Real target `.env` remains private deployment configuration.
- [x] PostgreSQL-backed GitHub workflow: migrate/check schema, run backend tests,
  fail when the database is unavailable instead of skipping DB tests. Frontend
  lifecycle tests, typecheck and production build included. Workflow execution
  on GitHub awaits push; native PostgreSQL verification is not that execution.
- [x] Ingestion rolling committed-sample rate and persisted invalid-message total,
  with process/persistence scope labels. Counters only increase after commit.
- [x] Grafana compose variables, configuration mount, separate dashboard path,
  PostgreSQL startup dependency, one default datasource and read-only role setup.
- [x] Authenticated embedding by default; anonymous viewing disabled. Local HTTP
  cookie settings differ explicitly from production cross-site HTTPS settings.
- [x] Grafana device SQL quoting, numeric cell-voltage extraction, real null-filled
  5-second missing-data buckets, event annotations instead of mixed-unit overlays.
- [x] React StrictMode polling startup/cancellation and device-switch isolation;
  desktop/mobile width fixes; UTC bucket parsing including half-hour offsets.
- [x] Report filters now apply to list, counts, hourly activity and recorded
  actions. Optional sections are honoured. No silent 1,000-row truncation:
  complete sections up to 10,000 rows; explicit 422 above that synchronous limit.
  `report-v2` identifies new snapshot semantics; old snapshots remain untouched.
- [x] All-cell blend's stale XGBoost-only UBJ/JSON removed; training cleans them
  up. Window and demo models retain their valid portable exports.
- [x] Browser live sample -> SSE -> Reporting without reload; anomaly detail
  navigation, acknowledge/action/two-step resolve, report generate/JSON download.
- [x] Real PostgreSQL, FastAPI, Grafana and React desktop/mobile native smoke.
  See `ACCEPTANCE_2026-10-22.md`. Test fixtures are labelled, not pack evidence.

## Still required for full frontend PRD completion

### Scope/architecture
- [x] Paginated PDF export from stored report snapshots (local, awaiting push review).
- [ ] Queued large-report generation and load testing: synchronous snapshots can
  occupy the API event loop; the 10,000-row cap is a guard, not a queue worker.
- [ ] Complete fleet-report telemetry and SoH aggregation. Current fleet reports
  contain anomaly/action data; device-only sections must not be implied complete.
- [ ] Add data-gap summaries to reports and make rejection counts period-scoped
  (the current rejection summary is all-time).

### Anomalies and analysis
- [x] FALSE_POSITIVE classification with mandatory explanation, persisted outcome, audit evidence and terminal-state handling (local feature commit, awaiting push review).
- [ ] Affected-cell and recurrence-frequency filtering; recurrence table column.
- [ ] Previous-equivalent-period comparison, category breakdown and 24h/7d/30d
  analysis controls; current page's custom dates are not the entire PRD.
- [ ] Validate before/during/after telemetry, recurrence outcomes and diagnosis
  context against the detailed PRD acceptance, not merely route availability.
- [ ] Per-page loading/error/empty/stale acceptance beyond the smoke recorded.
  Missing prediction is honest; production outage/auth behavior still needs checks.
- [ ] Grafana iframe cannot reliably detect cross-origin login/failure from
  onLoad alone. Improve explicit readiness/auth guidance and timeout/fallback.
- [ ] System Health model registry is provenance, not proof of successful current
  model load/inference. Add actual inference-status/error/latency observability.

### Deployment and release evidence
- [ ] Run the new CI on GitHub (requires an approved push).
- [ ] Run Docker compose on the target laptop/server. Native stack checks here
  do not validate image builds, ports or that host's network/cookie policy.
- [ ] Verify Grafana viewer login/embedding behind the actual deployment's HTTPS,
  authentication and reverse proxy, with a dedicated read-only DB role.
- [ ] Persist a browser regression suite in CI, including offline/error/empty,
  device switching, keyboard navigation and production bundle secret checks.
- [ ] Review security/authorization of read APIs and anomaly actions before any
  public deployment. Per-device ingest credentials do not protect all UI APIs.
- [ ] Confirm full-stack recovery/backpressure/resource behavior under expected
  device counts and retention volumes.

## Model boundary, not a replacement task

The 5-input partial-window model is the live backend model. The 39-input
XGBoost+ElasticNet blend needs a completed discharge and different features.
Swapping filenames would break the contract. A future completed-discharge API
needs its own feature/reference design and end-to-end validation. RUL remains
reference matching in the backend; offline RUL regression is separate.

## Physical work, outside the NASA software acceptance

- [ ] Actual ESP32/sensor wiring, flashing and sustained 1Hz MQTT ingestion.
- [ ] Own-cell charge/discharge data, pack validation and calibration.
- [ ] Hardware safety, chemistry thresholds and mentor acceptance.

Never use software acceptance fixtures, NASA validation or synthetic anomalies
as evidence of those physical outcomes. No tracker rows changed by this patch.
