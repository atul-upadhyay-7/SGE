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
| `/` | Overview | Live: connection, last telemetry, anomaly counts, predicted SoH, embedded 24 h telemetry |
| `/live` | Live Monitoring | Live: embedded Grafana 24 h telemetry, data-freshness/connection KPIs, shared SSE event log |
| `/anomalies` | Anomaly Center | Live: summary counts, filters, sortable/paginated table, hourly severity chart, CSV export |
| `/anomalies/:anomalyId` | Anomaly Details | Live: identification, measurements + trace, diagnosis, recommendations, resolution workflow |
| `/health` | Battery Health (SoH) | Live: latest predicted SoH, predicted-vs-measured history, model/feature metadata |
| `/reports` | Historical Reports | Live: generate from filters + section toggles, browse stored reports, render the persisted snapshot, export JSON/CSV |
| `/devices` | Device Management | Live: list (cells, rate, status, last seen, last telemetry, ingestion errors), registration, per-device details drill-down |
| `/system` | System Health | Live: independent panels for FastAPI, PostgreSQL, MQTT, ingestion, monitor, ML models and the live stream, plus last message per device |

Pages whose backend feature does not exist yet show an explicit
"not available yet" panel listing what they will contain — they never render
invented data.## Design notes

- **Dark monitoring theme** with severity colours (green normal, yellow warning,
  orange high-priority, red critical, blue voltage) defined as tokens in
  `src/styles/tokens.css`.
- **Accessibility**: severity and connection state are never conveyed by colour
  alone — every indicator carries a text label and a glyph.
- **Polling**: the 1 Hz hardware rate is never used to poll the API. Pages poll
  at 5–15 s and rely on the SSE stream for live updates.
- **Grafana**: the rolling 24-hour telemetry graphs are Grafana's job, embedded
  as a kiosk iframe (`now-24h` → `now`, auto-refresh) from `VITE_GRAFANA_URL`.
  Only the public base URL and dashboard UID reach the bundle — no Grafana admin
  or database credential is ever shipped. If the panel cannot be embedded, the
  page shows an honest fallback with a direct link, not a blank chart.
- **Data freshness**: connection status is derived from the newest stored sample
  against the device's sampling rate (`src/hooks/useDataFreshness.ts`), so a
  reachable API and a silent device are shown as different states (PRD §4.9).
- **Charts**: aggregated views that belong in the React app (hourly anomaly
  activity, SoH trend) are small dependency-free SVG charts in
  `src/components/charts/Charts.tsx`, each with a title, units, legend and an
  accessible name. Grafana owns the high-rate time series.
- **Diagnosis honesty**: anomaly causes are shown as "evidence-backed" only when
  the backend sets `confirmed: true`; everything else is labelled
  "unconfirmed" and the page never asserts a specific failed component
  (PRD §4.4).
- **One stream**: a single `EventSource` is opened by `StreamProvider` and shared
  through context, so the connection indicator and every event log observe the
  same connection.
- **Four states**: every data page renders loading, error (with retry), empty,
  and success, plus a last-updated freshness badge.
- **Accessibility**: a skip link jumps to `<main id="main-content">`, a global
  `:focus-visible` outline marks keyboard focus, clickable rows respond to
  Enter/Space, and severity/status are never conveyed by colour alone.
- **Responsive**: below 768 px the sidebar becomes a sticky horizontal nav and
  the shell stacks; KPI grids collapse to one column and wide tables scroll
  horizontally instead of overflowing.
- **Tables**: long tables (reports, devices, anomalies) scroll horizontally;
  numeric/monospace columns stay aligned.

## Layout

```
src/
  app/            App shell and route table
  components/
    cards/        Card, KpiCard, PageHeader
    charts/       GrafanaEmbed (kiosk iframe for the 24 h telemetry dashboard)
    feedback/     Loading/Error/Empty/Stale, severity + status pills, placeholders
    layout/       Sidebar, Topbar, DeviceSelector, ConnectionStatus, AppLayout
  hooks/          useAsync, usePolling, useEventStream (shared stream), useSelectedDevice, useDataFreshness
  pages/          One folder per PRD page
  services/       Typed API client (api, devices, telemetry, predictions, alerts, health, stream)
  styles/         Design tokens + global stylesheet
  types/          Domain types mirroring backend schemas
  utils/          Formatting helpers
```

## Deployment

The app is a static SPA (Vite build → `dist/`) that talks to the backend and,
optionally, Grafana. All three endpoints are build-time configuration and are
public values, never secrets.

1. Set the environment (copy `.env.example` → `.env`, or export in CI):

   | Variable | Purpose | Default |
   | --- | --- | --- |
   | `VITE_API_BASE_URL` | Backend base URL incl. `/api/v1` | `http://localhost:8000/api/v1` |
   | `VITE_GRAFANA_URL` | Grafana base URL (kiosk iframe) | `http://localhost:3000` |
   | `VITE_GRAFANA_DASHBOARD_UID` | Dashboard UID embedded on Live Monitoring | `bms-live` |

2. Build and serve:

   ```
   npm ci
   npm run build      # emits dist/
   npm run preview    # local check of the production build
   ```

3. Serve `dist/` from any static host. Because the app uses client-side routing,
   configure a **SPA fallback** (rewrite unknown paths to `index.html`):

   - nginx: `try_files $uri $uri/ /index.html;`
   - Netlify: `/* /index.html 200`
   - Vercel / SPA hosts: enable the SPA/rewrite preset.

4. Serve over HTTPS in production. The backend must allow the deployed origin
   via its `BMS_CORS_ALLOW_ORIGINS`. The browser reads the report download
   filename from `Content-Disposition`, which the backend exposes via
   `Access-Control-Expose-Headers`.

## Legacy

The previous static frontend (plain `index.html`/`app.js`/`styles.css`) is kept
under `_legacy/` for reference and is not built or served.
