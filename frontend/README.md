# BMS Monitor — Frontend

React + Vite + TypeScript single-page app for the Battery Management System.
It is a **client of the backend only**: every value comes from the FastAPI
backend over HTTP or the SSE event stream. The app never connects to
PostgreSQL, MQTT, or any data source directly.

## Requirements

- Node.js 18+ (developed against Node 22)
- A running backend (see `../backend/README.md`)

## Configuration

Copy `.env.example` to `.env` and adjust if needed. All values are public and
baked into the bundle at build time — never put a secret here.

| Variable | Default | Purpose |
| --- | --- | --- |
| `VITE_API_BASE_URL` | `http://localhost:8000/api/v1` | Backend base URL including the `/api/v1` prefix |
| `VITE_GRAFANA_URL` | `http://localhost:3000` | Grafana base URL for embedded panels |
| `VITE_GRAFANA_DASHBOARD_UID` | `bms-live` | Dashboard UID embedded on the Live Monitoring page |

The backend must allow this origin via CORS; its `BMS_CORS_ALLOW_ORIGINS`
already defaults to the Vite dev ports.

## Scripts

```bash
npm install
npm run dev        # Vite dev server on http://localhost:5173
npm run typecheck  # tsc project build (no emit)
npm run build      # typecheck + production bundle into dist/
npm run preview    # serve the built bundle
```

## Pages

| Route | Page | Status |
| --- | --- | --- |
| `/` | Overview | Live: device status, last telemetry, alert-derived counts |
| `/live` | Live Monitoring | Live: shared SSE event log + polled latest telemetry |
| `/anomalies` | Anomaly Center | Awaiting anomaly APIs |
| `/anomalies/:anomalyId` | Anomaly Details | Awaiting anomaly detail APIs |
| `/health` | Battery Health (SoH) | Awaiting SoH analytics view |
| `/reports` | Historical Reports | Awaiting report APIs |
| `/devices` | Device Management | Live: list + register |
| `/system` | System Health | Live: per-subsystem health panels |

Pages whose backend feature does not exist yet show an explicit
"not available yet" panel listing what they will contain — they never render
invented data.

## Design notes

- **Dark monitoring theme** with severity colours (green normal, yellow warning,
  orange high-priority, red critical, blue voltage) defined as tokens in
  `src/styles/tokens.css`.
- **Accessibility**: severity and connection state are never conveyed by colour
  alone — every indicator carries a text label and a glyph.
- **Polling**: the 1 Hz hardware rate is never used to poll the API. Pages poll
  at 5–15 s and rely on the SSE stream for live updates.
- **One stream**: a single `EventSource` is opened by `StreamProvider` and shared
  through context, so the connection indicator and every event log observe the
  same connection.
- **Four states**: every data page renders loading, error (with retry), empty,
  and success, plus a last-updated freshness badge.

## Layout

```
src/
  app/            App shell and route table
  components/
    cards/        Card, KpiCard, PageHeader
    feedback/     Loading/Error/Empty/Stale, severity + status pills, placeholders
    layout/       Sidebar, Topbar, DeviceSelector, ConnectionStatus, AppLayout
  hooks/          useAsync, usePolling, useEventStream (shared stream), useSelectedDevice
  pages/          One folder per PRD page
  services/       Typed API client (api, devices, telemetry, predictions, alerts, health, stream)
  styles/         Design tokens + global stylesheet
  types/          Domain types mirroring backend schemas
  utils/          Formatting helpers
```

## Legacy

The previous static frontend (plain `index.html`/`app.js`/`styles.css`) is kept
under `_legacy/` for reference and is not built or served.
