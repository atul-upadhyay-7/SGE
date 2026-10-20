/**
 * System Health page (PRD §4.9).
 *
 * Shows each backend subsystem's status *separately* — a healthy API does not
 * imply the BMS is sending data, and MQTT/DB/ingestion/monitor can each fail
 * independently. Every panel reads its own endpoint, so one failing subsystem
 * does not hide the others.
 */
import { Card, PageHeader } from "@/components/cards/Cards";
import {
  ErrorState,
  LoadingState,
  StaleBadge,
} from "@/components/feedback/Feedback";
import { usePolling } from "@/hooks/usePolling";
import {
  getDatabaseHealth,
  getHealth,
  getIngestionHealth,
  getMonitorHealth,
  getMqttHealth,
} from "@/services/health";
import type { HealthStatus } from "@/types/domain";

interface Panel {
  key: string;
  title: string;
  detail: string;
  load: (signal: AbortSignal) => Promise<HealthStatus>;
}

const PANELS: Panel[] = [
  {
    key: "api",
    title: "FastAPI",
    detail: "Application process and version",
    load: getHealth,
  },
  {
    key: "database",
    title: "PostgreSQL",
    detail: "Database connectivity",
    load: getDatabaseHealth,
  },
  {
    key: "mqtt",
    title: "MQTT broker",
    detail: "Broker and consumer connection",
    load: getMqttHealth,
  },
  {
    key: "ingestion",
    title: "Telemetry ingestion",
    detail: "Writer counters (stored, rejected, duplicates)",
    load: getIngestionHealth,
  },
  {
    key: "monitor",
    title: "Monitoring loop",
    detail: "Alert/aggregation loop status",
    load: getMonitorHealth,
  },
];

function HealthPanel({ panel }: { panel: Panel }) {
  const { data, error, loading, lastUpdated, refresh } = usePolling(
    panel.load,
    10_000,
  );
  const ok = data?.status === "ok";

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
          <div className={`health-banner health-banner--${ok ? "ok" : "error"}`}>
            <span aria-hidden="true">{ok ? "✓" : "✕"}</span>
            <span>{ok ? "OK" : "Error"}</span>
          </div>
          <p className="muted">{panel.detail}</p>
          <pre className="code">{JSON.stringify(data, null, 2)}</pre>
        </>
      )}
    </Card>
  );
}

export function SystemHealthPage() {
  return (
    <div className="page">
      <PageHeader
        title="System Health"
        subtitle="Backend subsystems — independent of battery health"
      />
      <div className="grid grid--2">
        {PANELS.map((panel) => (
          <HealthPanel key={panel.key} panel={panel} />
        ))}
      </div>
    </div>
  );
}
