# Frontend Product Requirements Document (PRD)
## Real-Time BMS Monitoring, Anomaly Intelligence & SoH Analytics

**Version:** 1.0  
**Project:** Real-Time BMS Logging and SoH Prediction  
**Frontend directory:** `frontend/`  
**Frontend stack:** React + Vite + TypeScript  
**Visualization:** Grafana  
**Backend:** FastAPI + PostgreSQL + MQTT  
**Data source:** ESP32-connected BMS  
**Sampling target:** 1 Hz

---

# 1. Product Objective

Build a professional battery monitoring application that combines live telemetry visualization with actionable anomaly intelligence and historical battery-health reporting.

The application has two complementary interfaces:

1. **Grafana:** Live telemetry graphs, rolling 24-hour trends, thresholds, and anomaly overlays.
2. **React application:** System overview, anomaly investigation, recommended corrective actions, recurrence analysis, SoH predictions, and complete reports.

The frontend must consume backend APIs and embed configured Grafana dashboards. It must not connect directly to PostgreSQL or MQTT.

# 2. Application Navigation

Create the following pages:

1. Overview
2. Live Monitoring
3. Anomaly Center
4. Anomaly Details
5. Battery Health (SoH)
6. Historical Reports
7. Device Management
8. System Health

Provide a persistent sidebar, top navigation, device selector, selected time range where applicable, and connection-status indicators.

# 3. Design Requirements

Use a dark, professional monitoring interface inspired by the supplied Grafana screenshots.

- Dark charcoal background with subtle grid lines.
- Green for normal operation.
- Yellow for warnings.
- Orange for high-priority anomalies.
- Red for critical anomalies.
- Blue for voltage and supporting telemetry.
- Responsive layout for desktop, tablet, and mobile.
- Consistent typography, spacing, cards, tables, and chart legends.
- Loading, empty, stale-data, disconnected, and error states on every relevant page.

Do not invent telemetry, prediction values, or anomaly events when the backend returns no data.

# 4. Page Specifications

## 4.1 Overview Dashboard

Provide an operational summary.

### KPI cards

- Current SoH
- Pack voltage
- Current
- Average temperature
- Number of active anomalies
- Critical anomalies
- Last telemetry received
- Device connection status

### Main visualizations

- Rolling 24-hour telemetry graph.
- Individual cell voltage comparison.
- Cell voltage imbalance.
- Temperature trend.
- Anomaly count over time.
- Recent anomaly events.

Clicking an anomaly opens its detailed investigation page. Clicking a telemetry chart opens Live Monitoring.

The dashboard must distinguish measured SoH from predicted SoH.

## 4.2 Live Monitoring — Grafana

Embed a dedicated Grafana dashboard inside the React application.

### Rolling 24-hour behavior

The primary graph must always show a moving 24-hour window.

- The right edge represents the current time.
- New telemetry enters from the right.
- Measurements older than 24 hours leave the visible window on the left.
- The chart updates automatically without requiring a page reload.
- The displayed time range remains fixed at 24 hours.
- Missing telemetry must appear as gaps rather than fabricated continuous data.
- Show the latest measurement timestamp and data freshness.

Use Grafana's relative time range `now-24h` to `now`, with automatic refresh configured appropriately for the ingestion rate.

The graph should visually resemble the first supplied reference image.

### Graphs to include

1. Pack voltage over time.
2. Current over time.
3. Individual cell voltages.
4. Temperature over time.
5. Cell voltage spread over time.
6. Anomaly score or anomaly indicator.
7. Anomaly events overlaid on relevant telemetry graphs.

Use separate axes or separate panels when units differ.

### Grafana implementation rules

- Create a provisioned dashboard and datasource configuration.
- Use the backend-supported datasource; do not assume InfluxDB exists.
- For the current PostgreSQL architecture, Grafana can query PostgreSQL directly through a read-only database account.
- Alternatively, use FastAPI-backed visualization data if direct database access is not permitted.
- Use an authenticated Grafana embedding configuration.
- Never expose Grafana administrator credentials or database write credentials in frontend code.

Grafana embedding must work in the actual deployment environment, including any required iframe and cookie configuration.

## 4.3 Anomaly Center

This page provides a comprehensive list of detected anomalies.

### Summary cards

- Total anomalies
- Critical anomalies
- Warnings
- Unresolved anomalies
- Repeated anomaly types
- Anomalies in the last 24 hours

### Anomaly table

Columns:

- Event ID
- Detected time
- Device
- Anomaly category
- Severity
- Affected cell or component
- Observed measurement
- Expected range or threshold
- Duration
- Recurrence count
- Status
- Action

### Filters

- Device
- Start and end time
- Severity
- Anomaly category
- Affected cell
- Open/resolved status
- Recurrence frequency

Support sorting, pagination, and CSV export.

The layout should follow the second reference image's emphasis on time-bucketed anomaly activity, with an additional event investigation table.

## 4.4 Anomaly Details and Investigation

Every anomaly must have a dedicated details view.

### Required information

**Identification**
- Event ID
- Anomaly type
- Severity
- Device and affected cell
- First detected time
- Last detected time
- Duration
- Current status

**Measurements**
- Actual observed value
- Expected value or configured threshold
- Deviation magnitude
- Relevant telemetry graph around the event
- Before, during, and after measurements

**Diagnosis**
- Likely causes
- Supporting evidence
- Confidence or evidence strength, where available
- Related anomalies
- Historical recurrence count
- Previous occurrences and their outcomes

**Recommended corrective actions**
- Immediate safety precautions, where appropriate
- Diagnostic checks
- Recommended maintenance action
- Verification steps after the action
- Escalation recommendation

**Resolution workflow**
- Acknowledge anomaly
- Assign status
- Add investigation notes
- Record action taken
- Mark resolved
- Record resolution time and outcome

Possible statuses: `OPEN`, `ACKNOWLEDGED`, `INVESTIGATING`, `RESOLVED`, `FALSE_POSITIVE`.

The backend must persist all state changes and investigation notes.

### Important diagnostic rule

The system must distinguish detected facts from possible explanations. Do not present a likely cause as confirmed unless supported by validated diagnostic logic.

For example, a cell-voltage imbalance may warrant checking cell measurements, connections, and balancing behavior. The application must not claim a specific failed component solely from that anomaly.

## 4.5 Anomaly Frequency and Hourly Activity

Provide an hourly stacked bar chart similar to the second reference screenshot.

Display:
- Anomaly count per hour.
- Breakdown by anomaly category or affected component.
- Severity distribution.
- Peak activity periods.
- Repeated anomalies by device.
- Comparison with the previous equivalent period.

Support 24-hour, 7-day, and 30-day analysis.

All charts must use explicit timezone handling. UTC should be stored in the database, with display timezone configured for the user.

The chart must use actual aggregated event data, not arbitrary sample values.

## 4.6 Battery Health — SoH

Display the current available SoH prediction and its history.

### Required elements

- Latest predicted SoH percentage.
- Prediction timestamp.
- Model version.
- Historical SoH trend.
- Cycle number or feature-window reference, when available.
- Prediction confidence or uncertainty interval only if the model provides a validated estimate.
- Input data quality and feature availability.
- Historical measured SoH for comparison, when available.

Clearly label predicted and measured values separately.

If the existing model requires cycle-level features, the UI must show a pending or unavailable state until sufficient data has been collected and valid features have been generated.

Never display a fabricated SoH prediction simply because live telemetry is available.

## 4.7 Historical Reports

Provide a complete report-generation interface.

### Report filters

- Device
- Start and end time
- Report type
- Severity
- Anomaly category
- Include telemetry summaries
- Include SoH history
- Include anomaly details
- Include corrective actions

### Report contents

1. Executive summary.
2. Reporting period and device details.
3. Data availability and telemetry gaps.
4. Voltage, current, and temperature summaries.
5. SoH prediction history.
6. Anomaly count and severity breakdown.
7. Hourly/daily anomaly frequency.
8. Detailed anomaly event list.
9. Likely causes and supporting evidence.
10. Recommended corrective actions.
11. Recorded actions and resolution outcomes.
12. Unresolved anomalies.
13. Model and report-generation metadata.

### Export formats

- CSV for raw tabular exports.
- PDF for complete reports.
- JSON for structured data exports.

Reports must use persisted data and reproducible aggregation logic. Long-running report generation should not block live ingestion.

## 4.8 Device Management

Display:

- Device identifier.
- Device name.
- Configured cell count.
- Expected sampling rate.
- Last-seen timestamp.
- Connection status.
- Last received telemetry.
- Recent ingestion errors.

Support device registration and viewing device details. Device configuration changes must be validated by the backend.

## 4.9 System Health

Display operational health separately from battery health.

Include:

- FastAPI status.
- PostgreSQL connectivity.
- MQTT broker and consumer status.
- Last received message per device.
- Telemetry ingestion rate.
- Latest stored measurement.
- Invalid message count.
- Recent backend errors.
- ML model loading and inference status.

A healthy API does not necessarily mean the BMS is sending data. These statuses must remain distinct.

# 5. Frontend Technical Architecture

Suggested directory structure:

```text
frontend/
├── src/
│   ├── app/
│   │   ├── App.tsx
│   │   └── router.tsx
│   ├── components/
│   │   ├── layout/
│   │   ├── charts/
│   │   ├── cards/
│   │   ├── tables/
│   │   └── feedback/
│   ├── pages/
│   │   ├── Overview/
│   │   ├── LiveMonitoring/
│   │   ├── AnomalyCenter/
│   │   ├── AnomalyDetails/
│   │   ├── BatteryHealth/
│   │   ├── Reports/
│   │   ├── Devices/
│   │   └── SystemHealth/
│   ├── features/
│   │   ├── telemetry/
│   │   ├── anomalies/
│   │   ├── predictions/
│   │   └── reports/
│   ├── services/
│   │   ├── api.ts
│   │   ├── telemetry.ts
│   │   ├── anomalies.ts
│   │   └── predictions.ts
│   ├── hooks/
│   ├── types/
│   ├── utils/
│   └── styles/
├── public/
├── .env.example
├── package.json
└── README.md
```

### Frontend requirements

- React + Vite + TypeScript.
- React Router for navigation.
- A consistent component library and charting approach.
- A typed API client.
- Environment-based backend URL.
- Configurable Grafana base URL and dashboard identifiers.
- Loading, error, empty, stale-data, and retry handling.
- No hardcoded telemetry or anomaly records in production pages.

Example environment variables:

```env
VITE_API_BASE_URL=http://localhost:8000/api/v1
VITE_GRAFANA_URL=http://localhost:3000
```

Use a development-only Grafana URL if required. These variables are public frontend configuration, not secrets.

# 6. Data Refresh and Real-Time Behavior

- Grafana handles rolling time-series refresh.
- React fetches current telemetry and device status periodically.
- Anomaly lists refresh periodically or through a future event stream.
- Historical reports are fetched on demand.
- Prediction history is refreshed independently of raw telemetry.
- Avoid full-page refreshes.
- Prevent overlapping polling requests.
- Display the last successful refresh time.
- Show stale-data warnings when telemetry stops arriving.

The 1 Hz hardware sampling target does not require every React component to poll once per second. Keep UI refresh independent from raw ingestion frequency.

# 7. Accessibility and UX

- Charts must have titles, units, legends, and readable axes.
- Severity must not be communicated through color alone.
- Tables must support keyboard navigation.
- Empty states must explain why data is unavailable.
- Confirmation is required for irreversible actions.
- All timestamps must be clearly labeled with their timezone.
- Critical alerts must remain visible until acknowledged or otherwise handled according to the defined workflow.

# 8. Frontend Acceptance Criteria

1. Every specified page loads and navigates correctly.
2. The Grafana panel displays a rolling 24-hour window.
3. New telemetry appears without a page reload.
4. Older readings leave the displayed time window.
5. Missing telemetry appears as a gap.
6. Anomaly events link to their detailed investigation.
7. Causes and recommendations are grounded in backend diagnostic results.
8. Hourly frequency charts use actual persisted anomaly events.
9. SoH history distinguishes predicted and measured values.
10. Reports support the specified filters and export formats.
11. Device and backend health states remain distinct.
12. All pages handle loading, error, empty, and stale states.
13. No credentials or secrets are embedded in the frontend bundle.
14. The frontend works against the real FastAPI backend without mock data in production mode.

# 9. Implementation Milestones

**Milestone 1 — UI foundation**
- App layout, routing, design system, environment configuration, API client.

**Milestone 2 — Live monitoring**
- Grafana dashboard embedding, 24-hour rolling window, live device status.

**Milestone 3 — Anomaly intelligence**
- Anomaly Center, details page, hourly charts, diagnosis, recommendations, resolution workflow.

**Milestone 4 — SoH analytics**
- Predictions, history, model metadata, data-quality indicators.

**Milestone 5 — Reports**
- Filtering, report generation, CSV/PDF/JSON export.

**Milestone 6 — Integration and quality**
- End-to-end testing, responsive layout, error states, accessibility, deployment configuration.

# 10. Definition of Done

The frontend is complete when it consumes the actual backend, embeds the configured Grafana dashboard, displays the rolling 24-hour telemetry view, supports anomaly investigation and resolution, exposes SoH history, and generates reproducible historical reports.

The frontend must not independently invent anomaly diagnoses, corrective actions, SoH values, or recurrence statistics. Those must originate from backend data or explicitly identified diagnostic rules.
