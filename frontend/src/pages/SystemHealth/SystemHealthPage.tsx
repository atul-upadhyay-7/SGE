/**
 * System Health page (PRD §4.9).
 *
 * Shows each backend subsystem's status *separately* — a healthy API does not
 * imply the BMS is sending data, and MQTT/DB/ingestion/monitor/ML/stream can
 * each fail independently. Every panel reads its own endpoint, so one failing
 * subsystem does not hide the others.
 *
 * Writer rates are process-local; the invalid-message total is persisted.
 */
import { Card, PageHeader } from "@/components/cards/Cards";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  StaleBadge,
} from "@/components/feedback/Feedback";
import { StatusPill } from "@/components/feedback/Indicators";
import { usePolling } from "@/hooks/usePolling";
import { useStream } from "@/hooks/useEventStream";
import { getActiveModels } from "@/services/predictions";
import {
  getDatabaseHealth,
  getHealth,
  getIngestionHealth,
  getMonitorHealth,
  getInferenceHealth,
  getMqttHealth,
} from "@/services/health";
import { getStreamStatus } from "@/services/stream";
import { getTelemetrySummary } from "@/services/telemetry";
import { listDevices } from "@/services/devices";
import type { HealthStatus } from "@/types/domain";
import { formatDateTime, relativeTime } from "@/utils/format";

interface Panel {
  key: string;
  title: string;
  detail: string;
  load: (signal: AbortSignal) => Promise<HealthStatus>;
  render: (data: HealthStatus) => React.ReactNode;
}

const PANELS: Panel[] = [
  {
    key: "inference",
    title: "Observed SoH inference",
    detail: "Actual process-local calls, not registry metadata or a health-check prediction",
    load: getInferenceHealth,
    render: d => <Fields rows={[
      ["Result", String(d.last_result ?? 'No calls observed')],
      ["Artifact present", yesNo(d.artifact_present)],
      ["Model loaded", yesNo(d.model_loaded)],
      ["Attempts", num(d.attempts)],
      ["Successes", num(d.successes)],
      ["Input refusals", num(d.refused)],
      ["Errors", num(d.errors)],
      ["Last success", formatDateTime(asString(d.last_success_at))],
      ["Last call latency (ms)", num(d.last_latency_ms)],
      ["Last error type", String(d.last_error_type ?? 'none')],
      ["Anomaly engine", String(d.anomaly_engine)],
    ]} />,
  },
  {
    key: "api",
    title: "FastAPI",
    detail: "Application process and version",
    load: getHealth,
    render: (d) => (
      <Fields
        rows={[
          ["Application", String(d.app ?? "—")],
          ["Version", String(d.version ?? "—")],
          ["Environment", String(d.environment ?? "—")],
        ]}
      />
    ),
  },
  {
    key: "database",
    title: "PostgreSQL",
    detail: "Database connectivity",
    load: getDatabaseHealth,
    render: (d) => (
      <Fields
        rows={[
          ["Database", String(d.database ?? "—")],
          ...(d.detail ? ([["Detail", String(d.detail)]] as [string, string][]) : []),
        ]}
      />
    ),
  },
  {
    key: "mqtt",
    title: "MQTT broker",
    detail: "Broker and consumer connection",
    load: getMqttHealth,
    render: (d) => (
      <Fields
        rows={[
          ["State", String(d.mqtt ?? "—")],
          ["Enabled", yesNo(d.enabled)],
          ["Host", d.host ? `${d.host}:${d.port}` : "—"],
          ["Topic", String(d.topic ?? "—")],
          ["Connected since", formatDateTime(asString(d.connected_since))],
          ["Reconnects", num(d.reconnects)],
          ["Messages received", num(d.messages_received)],
          ["Last error", asString(d.last_error) ?? "none"],
        ]}
      />
    ),
  },
  {
    key: "ingestion",
    title: "Telemetry ingestion",
    detail: "Writer counters (cumulative for this process)",
    load: getIngestionHealth,
    render: (d) => (
      <Fields
        rows={[
          ["State", String(d.ingestion ?? "—")],
          ["Queued", num(d.queued)],
          ["Stored", num(d.stored)],
          ["Duplicates dropped", num(d.duplicates)],
          ["Rejected (invalid)", num(d.rejected)],
          ["Dropped (queue overflow)", num(d.dropped)],
          ["Write errors", num(d.write_errors)],
          ["Stored samples / second (last 60s or since startup)",
            typeof d.stored_per_second === "number" ? d.stored_per_second.toFixed(2) : "unavailable"],
          ["Invalid messages (persisted, all devices)", num(d.persisted_invalid_count)],
          ["Counter scope", String(d.counter_scope ?? "unavailable")],
        ]}
      />
    ),
  },
  {
    key: "monitor",
    title: "Monitoring loop",
    detail: "Alert / aggregation loop",
    load: getMonitorHealth,
    render: (d) => (
      <Fields
        rows={[
          ["State", String(d.monitor ?? "—")],
          ["Enabled", yesNo(d.enabled)],
          ["Interval", d.interval_s ? `${d.interval_s}s` : "—"],
          ["Auto-aggregate", yesNo(d.auto_aggregate)],
          ["Runs", num(d.runs)],
          ["Last run", formatDateTime(asString(d.last_run_at))],
          ["Last error", asString(d.last_error) ?? "none"],
        ]}
      />
    ),
  },
];

function HealthPanel({ panel }: { panel: Panel }) {
  const { data, error, loading, lastUpdated, refresh } = usePolling(
    panel.load,
    10_000,
  );
  const ok = data?.status === "ok";
  const waiting = data?.status === "not_ready";
  const tone = ok ? "ok" : waiting ? "warning" : "error";

  return (
    <Card
      title={panel.title}
      actions={<StaleBadge lastUpdated={lastUpdated} />}
      className="card--health"
    >
      {loading && !data && <LoadingState />}
      {error && <ErrorState message={error} onRetry={refresh} />}
      {data && (
        <>
          <div className={`health-banner health-banner--${tone}`}>
            <span aria-hidden="true">{ok ? "✓" : waiting ? "…" : "✕"}</span>
            <span>{ok ? "OK" : waiting ? "Not yet exercised" : "Error"}</span>
          </div>
          <p className="muted">{panel.detail}</p>
          {panel.render(data)}
        </>
      )}
    </Card>
  );
}

export function SystemHealthPage() {
  const { state, lastEventAt, events } = useStream();
  const overview = usePolling(getHealth, 10_000);

  return (
    <div className="page">
      <PageHeader
        title="System Health"
        subtitle="Backend subsystems — independent of battery health"
        actions={<StaleBadge lastUpdated={overview.lastUpdated} />}
      />

      <div className="grid grid--2">
        {PANELS.map((panel) => (
          <HealthPanel key={panel.key} panel={panel} />
        ))}
        <MlModelsPanel />
        <StreamPanel state={state} lastEventAt={lastEventAt} count={events.length} />
      </div>

      <DeviceLastMessageCard />
    </div>
  );
}

/** ML model loading/inference status (PRD §4.9), from the active-model registry. */
function MlModelsPanel() {
  const { data, error, loading, lastUpdated, refresh } = usePolling(
    getActiveModels,
    30_000,
  );
  const ok = !!data && data.count > 0;

  return (
    <Card
      title="ML models"
      actions={<StaleBadge lastUpdated={lastUpdated} />}
      className="card--health"
    >
      {loading && !data && <LoadingState />}
      {error && <ErrorState message={error} onRetry={refresh} />}
      {data && (
        <>
          <div className={`health-banner health-banner--${ok ? "ok" : "warn"}`}>
            <span aria-hidden="true">{ok ? "✓" : "!"}</span>
            <span>{ok ? "Active" : "No active model"}</span>
          </div>
          <p className="muted">
            Inference runs on cycle aggregation, not on raw readings (PRD §4.6).
          </p>
          {data.count === 0 ? (
            <p className="muted">No model is currently active.</p>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Model</th>
                    <th>Version</th>
                    <th>Features</th>
                    <th>Loaded</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((model) => (
                    <tr key={`${model.model_name}-${model.model_version}`}>
                      <td className="mono">{model.model_name}</td>
                      <td className="mono">{model.model_version}</td>
                      <td className="mono">{model.feature_schema_version}</td>
                      <td title={formatDateTime(model.created_at)}>
                        {relativeTime(model.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </Card>
  );
}

/** Live SSE stream health: the browser connection plus the server-side counters. */
function StreamPanel({
  state,
  lastEventAt,
  count,
}: {
  state: string;
  lastEventAt: Date | null;
  count: number;
}) {
  const status = usePolling(getStreamStatus, 30_000);
  const connected = state === "open";

  return (
    <Card
      title="Live stream"
      actions={<StaleBadge lastUpdated={status.lastUpdated} />}
      className="card--health"
    >
      <div className={`health-banner health-banner--${connected ? "ok" : "error"}`}>
        <span aria-hidden="true">{connected ? "✓" : "✕"}</span>
        <span>{connected ? "Connected" : state}</span>
      </div>
      <p className="muted">Server-sent events pushing live updates to this browser.</p>
      {status.error && <ErrorState message={status.error} onRetry={status.refresh} />}
      {status.data && (
        <Fields
          rows={[
            ["Subscribers", num(status.data.subscribers)],
            ["Published events", num(status.data.published)],
            ["Events received (this tab)", String(count)],
            ["Last event", lastEventAt ? relativeTime(lastEventAt.toISOString()) : "—"],
          ]}
        />
      )}
    </Card>
  );
}

/**
 * "Last received message per device" (PRD §4.9). Reads each device's telemetry
 * summary; a device that never sent data shows as such rather than as an error.
 */
function DeviceLastMessageCard() {
  const { data, error, loading, refresh } = usePolling(
    (signal) => listDevices(signal),
    30_000,
  );

  return (
    <Card title="Last received message per device" className="card--wide">
      {loading && !data && <LoadingState />}
      {error && <ErrorState message={error} onRetry={refresh} />}
      {data && data.length === 0 && (
        <EmptyState
          title="No devices registered"
          detail="Register a device to begin ingesting telemetry."
        />
      )}
      {data && data.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Device</th>
                <th>Status</th>
                <th>Last seen</th>
                <th>Last telemetry</th>
                <th>Samples</th>
                <th>Invalid</th>
              </tr>
            </thead>
            <tbody>
              {data.map((device) => (
                <DeviceHealthRow key={device.device_id} deviceId={device.device_id} status={device.status} lastSeen={device.last_seen_at} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function DeviceHealthRow({
  deviceId,
  status,
  lastSeen,
}: {
  deviceId: string;
  status: string;
  lastSeen: string | null;
}) {
  const summary = usePolling(
    (signal) => getTelemetrySummary(deviceId, {}, signal),
    15_000,
    [deviceId],
  );

  return (
    <tr>
      <td className="mono">{deviceId}</td>
      <td>
        <StatusPill status={status} />
      </td>
      <td title={formatDateTime(lastSeen)}>{relativeTime(lastSeen)}</td>
      <td title={formatDateTime(summary.data?.last_measured_at)}>
        {summary.loading && !summary.data
          ? "…"
          : summary.error
            ? "—"
            : relativeTime(summary.data?.last_measured_at ?? null)}
      </td>
      <td>{summary.data?.sample_count ?? "—"}</td>
      <td>
        {summary.error ? (
          "—"
        ) : summary.data && summary.data.rejection_count > 0 ? (
          <span className="pill pill--status-failed">
            {summary.data.rejection_count}
          </span>
        ) : (
          <span className="pill pill--status-ready">none</span>
        )}
      </td>
    </tr>
  );
}

function Fields({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="detail-grid">
      {rows.map(([label, value]) => (
        <div className="detail" key={label}>
          <dt className="detail__label">{label}</dt>
          <dd className="detail__value mono">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function num(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return String(value);
}

function yesNo(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return value ? "yes" : "no";
}

function asString(value: unknown): string | undefined {
  if (value === null || value === undefined) return undefined;
  return String(value);
}
