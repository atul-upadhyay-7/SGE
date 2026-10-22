# Project TODO / Remaining Work

> Status snapshot for the SGE — Battery Predictive Maintenance System.
> Scope covered here: the **BMS backend** (`backend/`), the **React frontend**
> (`frontend/`), and the integration change request documented in `CHANGES.md`.
> This file lists what is **done**, what is **left**, and **what to do next**.
> Everything described below is currently **uncommitted** in the working tree —
> do not commit unless explicitly asked.

---

## 1. What is done

### 1.1 Backend (`backend/`) — FastAPI + PostgreSQL + MQTT + ML
- **Phases 1–8 and Stages 1–11 are complete and green.**
- Full test suite: **197 passed** (run with a local PostgreSQL).
- Implemented subsystems:
  - Ingestion over MQTT (`POST /devices/{device_id}/telemetry` contract unchanged)
    with validation, dedupe, rejection reasons and quality flags.
  - Telemetry storage + read APIs.
  - SoH prediction (`MODEL_NAME="soh_window_all"`, `FEATURE_SCHEMA_VERSION="window-v1"`).
  - Anomaly detection + lifecycle (`anomaly_events`, `anomaly_actions`) with a
    configurable-per-device thresholds model (chemistry defaults:
    `li_ion`/`lifepo4`/`nimh`/`lead_acid`; unknown → `li_ion`).
  - Reports (JSON + CSV; synchronous; persisted JSON snapshot;
    `format_version="report-v1"`).
  - Health endpoints (`/health`, `/health/database`, `/health/mqtt`,
    `/health/ingestion`, `/health/monitor`).
  - Server-Sent Events stream (`/stream`, `/stream/status`).
- Grafana integration is **config-only** (`docker/grafana/**`) — no running
  Grafana is required.

### 1.2 Frontend (`frontend/`) — React + Vite + TypeScript
- **Milestones 1–6 implemented** per `frontend/prd.md`:
  - M1 UI foundation (layout, routing, design tokens, API client).
  - M2 Live Monitoring (Grafana kiosk embed + freshness/stream KPIs).
  - M3 Anomaly Center + Details (filters, sortable/paginated table, hourly
    stacked chart, CSV export, diagnosis split, resolution workflow).
  - M4 SoH analytics (`BatteryHealthPage`).
  - M5 Reports (`ReportsPage`, `services/reports.ts` — generation form, stored
    list, detail renderer, JSON/CSV blob export).
  - M6 Integration & quality: `DevicesPage` (§4.8), `SystemHealthPage` (§4.9),
    responsive breakpoints, accessibility (skip link, focus-visible, `aria-sort`,
    two-step confirm on destructive actions), README deployment section.
- **Acceptance-criteria hardening pass complete** (§6/§7/§8 audit closed):
  - Chart units + x-axis labels (`Charts.tsx`).
  - `aria-sort` + `scope="col"` on sortable headers.
  - Stale/critical banners on Overview; persistent critical-anomaly alert.
  - Query-param deep links into Anomaly Center.
  - Local-timezone labelling of hourly buckets and chart axes.
  - `ConfirmButton` two-step confirm for the irreversible resolve action.

### 1.3 Verification performed
- `npm run typecheck` clean; `npm run build` succeeds.
- All touched frontend modules transpile through Vite (HTTP 200).
- Live backend shape smoke matched page expectations
  (`/devices` = bare `Device[]`; telemetry latest/summary, anomalies filter,
  predictions history all correct).
- Backend suite: **197 passed**.
- End-to-end: REST smoke (17 checks), SSE frames, CORS preflight, Reports module
  served on port 5173.

---

## 2. What is left (required to finish the project)

### 2.1 Must-do before any delivery

1. **Commit the work.** The entire tree is uncommitted (backend Phases 1–8 +
   Stages 1–11, frontend M1–M6, hardening pass, `docker/grafana/**`).
   → Only do this when explicitly requested. Nothing is committed by default.

2. **Provide a real PostgreSQL for tests/CI.** Docker Desktop is unavailable on
   this machine, so tests currently require a throwaway PostgreSQL on
   **port 55433** with `BMS_DATABASE_URL` exported in the same shell:
   ```
   $env:BMS_DATABASE_URL="postgresql+asyncpg://bms:bms@127.0.0.1:55433/bms"
   ```
   Without it, DB-backed tests skip. → Decide the CI database strategy
   (service container / managed instance) and wire it into CI.

3. **Confirm the frontend runs only against the real backend in production
   mode.** No `.env` file exists — defaults in `src/config.ts`
   (`VITE_API_BASE_URL=http://localhost:8000/api/v1`,
   `VITE_GRAFANA_URL=http://localhost:3000`,
   `VITE_GRAFANA_DASHBOARD_UID=bms-live`) are used. → Create
   `frontend/.env.example` and a real `.env` for the target environment, and
   verify no mock/fixture data path is reachable in a production build.

4. **Browser-level end-to-end pass.** So far verification is HTTP-contract + Vite
   transpile level (no browser automation). → Run the acceptance criteria
   (§8) in a real browser against the live backend + Grafana to close
   AC #1–#14, especially #2 (rolling 24 h Grafana window), #3/#4 (SSE without
   reload, older readings leaving the window), #5 (missing telemetry as a gap),
   and #12 (loading/error/empty/stale on every page).

5. **Grafana dashboard wiring.** Config-only artifacts exist but no running
   Grafana is provisioned here. → Stand up Grafana (e.g. via
   `docker/grafana/**`), point it at the backend, and confirm the rolling
   24-hour dashboard renders and embeds in the kiosk frame.

### 2.2 Known gaps to resolve (UI-level)

1. **Backend fields the UI intentionally labels `not exposed by backend`:**
   ingestion rate and a global invalid-message count are **not** exposed.
   The System Health page honestly shows "not exposed by backend" for these.
   → Either add the endpoint(s) in the backend, or accept the current labels
   and document the decision.

2. **Report formats.** Only **JSON and CSV** are supported (PDF request → 400).
   The PRD Milestone 5 line mentions "CSV/PDF/JSON export".
   → Confirm PDF is out of scope, or implement PDF export server-side.

3. **Charting.** The charts are a dependency-free SVG implementation in
   `components/charts/Charts.tsx` (no charting library). This is deliberate.
   → Only replace if a richer interaction is required by the PRD.

### 2.3 Environment / tooling notes (not blockers, but plan around them)

- **Windows quirks:** no `rg` (use `grep`); no `psql.exe`/PostgreSQL on PATH;
  no PowerShell heredocs; `Start-Process` on chained commands may show a
  wrapper kill error — always re-check the port after launching.
- **Docker Desktop unavailable** → the compose PostgreSQL on **5433** is
  unusable here; use the throwaway instance on **55433**.
- Vite/uvicorn dev servers must be stopped and temp files cleaned after smoke
  runs; ports 8000 and 5173 must be freed.

---

## 3. What to do next (ordered checklist)

- [ ] Create `frontend/.env.example` (and target `.env`); verify production
      build uses only real backend calls (AC #13/#14).
- [ ] Decide and wire the CI PostgreSQL strategy; make the backend suite run
      unattended (no manual `BMS_DATABASE_URL`).
- [ ] Stand up Grafana from `docker/grafana/**`; verify the rolling 24-hour
      dashboard and its kiosk embed (AC #2).
- [ ] Run the full §8 acceptance criteria in a real browser against the live
      stack; fix anything that fails (esp. #3/#4/#5/#12).
- [ ] Resolve the two UI "not exposed by backend" fields (expose or document).
- [ ] Confirm PDF report scope (implement or explicitly drop).
- [ ] Re-run: `npm run typecheck`, `npm run build`, backend `pytest`
      (expect 197), and a live REST/SSE shape smoke.
- [ ] Stop all dev servers, free ports 8000/5173, remove temp files.
- [ ] **Only if asked:** commit the work.

---

## 4. Definition of Done (from `frontend/prd.md` §10)

The frontend is complete when it:
- consumes the **actual backend**,
- embeds the **configured Grafana dashboard**,
- displays the **rolling 24-hour telemetry view**,
- supports **anomaly investigation and resolution**,
- exposes **SoH history**, and
- generates **reproducible historical reports**.

The frontend must **not** independently invent anomaly diagnoses, corrective
actions, SoH values, or recurrence statistics — those must originate from
backend data or explicitly identified diagnostic rules.

---

## 5. Boundaries / out of scope for this project

From `README.md` and `CHANGES.md`, these are explicitly **not** part of the
current deliverable and should not be treated as bugs to fix here:
- No field validation on real packs; all ML numbers are on NASA lab cells.
- RUL is a **reference spread**, not a calibrated prediction interval.
- No external email/pager endpoint; alerts go to a local mock receiver.
- No ESP32 hardware validation.
- The online double-exponential fit is marked **legacy** and is not reported.
