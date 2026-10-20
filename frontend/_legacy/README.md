# BMS Monitor (minimal frontend)

A single-page, dependency-free dashboard for the FastAPI backend. It lists
devices, registers new ones, shows a selected device's latest telemetry, RUL
and open alerts, and subscribes to the backend's SSE channel
(`GET /api/v1/stream`) for live updates.

No build step: three static files (`index.html`, `styles.css`, `app.js`).

## Run

Start the backend first (from `backend/`):

```
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then serve these files from any static server, e.g. Python's:

```
python -m http.server 5173 --directory frontend
```

Open http://localhost:5173.

The API base defaults to `http://localhost:8000` and is editable in the top
bar (persisted to `localStorage`), so the same files work against a remote
backend without editing code.

## CORS

The backend allows the local dev origins by default
(`BMS_CORS_ALLOW_ORIGINS`). If you serve this on another origin, add it there,
e.g. `BMS_CORS_ALLOW_ORIGINS=http://localhost:5173,https://my-frontend.example`.

## What it covers

- Device list + registration (`POST /devices`, optional `X-Admin-Key`).
- Per-device: latest telemetry, RUL estimate, open alerts.
- Live event log driven by the SSE stream (telemetry, cycle, alert, device,
  monitor events).

## Not included (deliberately)

- Auth/session UI, routing, charts, historical navigation. Add when the
  product shape is known; the REST + SSE surface is stable enough to build on.
