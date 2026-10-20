/**
 * Overview dashboard (PRD §4.1).
 *
 * Phase 1 wires the pieces the backend already exposes (device connection
 * status, last telemetry received, active/critical anomaly counts via the
 * alert rules) and marks the remaining KPIs and charts as awaiting their
 * milestone. No value is fabricated: a KPI with no backend source shows a
 * pending state, not a placeholder number.
 */
import { KpiCard, Card, PageHeader } from "@/components/cards/Cards";
import { EmptyState, ErrorState, LoadingState, StaleBadge } from "@/components/feedback/Feedback";
import { SeverityPill, StatusPill } from "@/components/feedback/Indicators";
import { useAsync } from "@/hooks/useAsync";
import { usePolling } from "@/hooks/usePolling";
import { useSelectedDevice } from "@/hooks/useSelectedDevice";
import { listAlerts } from "@/services/alerts";
import { getDevice } from "@/services/devices";
import { getLatestTelemetry } from "@/services/telemetry";
import { formatDateTime, relativeTime } from "@/utils/format";

export function OverviewPage() {
  const { deviceId } = useSelectedDevice();

  const deviceState = useAsync(
    (signal) => (deviceId ? getDevice(deviceId, signal) : Promise.resolve(null)),
    [deviceId],
  );
  const telemetryState = usePolling(
    (signal) =>
      deviceId ? getLatestTelemetry(deviceId, signal) : Promise.resolve(null),
    10_000,
    [deviceId],
  );
  const alertState = useAsync(
    (signal) => listAlerts({ limit: 20 }, signal),
    [],
  );

  if (!deviceId) {
    return (
      <div className="page">
        <PageHeader title="Overview" subtitle="Operational summary" />
        <Card>
          <EmptyState
            title="Select a device"
            detail="Choose a device from the selector above to see its operational summary."
          />
        </Card>
      </div>
    );
  }

  const alerts = alertState.data?.items ?? [];
  const activeAlerts = alerts.filter((a) => a.status !== "resolved");
  const criticalAlerts = activeAlerts.filter(
    (a) => a.severity === "critical",
  );
  const telemetry = telemetryState.data;
  const device = deviceState.data;

  const connected =
    telemetry && device
      ? Date.now() - new Date(telemetry.measured_at).getTime() <
        Math.max(device.sampling_frequency_hz, 1) * 1000 * 60 * 5
      : false;

  return (
    <div className="page">
      <PageHeader
        title="Overview"
        subtitle={device ? `${device.name} · ${device.device_id}` : deviceId}
        actions={<StaleBadge lastUpdated={telemetryState.lastUpdated ?? null} />}
      />

      <div className="kpi-grid">
        <KpiCard
          label="Active anomalies"
          value={activeAlerts.length}
          tone={activeAlerts.length > 0 ? "warn" : "ok"}
        />
        <KpiCard
          label="Critical anomalies"
          value={criticalAlerts.length}
          tone={criticalAlerts.length > 0 ? "danger" : "ok"}
        />
        <KpiCard
          label="Pack voltage"
          value={telemetry?.pack_voltage ?? "—"}
          unit="V"
          tone="info"
        />
        <KpiCard label="Current" value={telemetry?.current ?? "—"} unit="A" />
        <KpiCard
          label="Temperature"
          value={telemetry?.temperature ?? "—"}
          unit="°C"
        />
        <KpiCard
          label="Last telemetry"
          value={telemetry ? relativeTime(telemetry.measured_at) : "—"}
          hint={telemetry ? formatDateTime(telemetry.measured_at) : undefined}
          tone={connected ? "ok" : "warn"}
        />
        <KpiCard
          label="Current SoH"
          value="—"
          hint="Pending: SoH analytics milestone"
          tone="default"
        />
        <KpiCard
          label="Device status"
          value={device ? <StatusPill status={device.status} /> : "—"}
          tone={connected ? "ok" : "warn"}
          hint={connected ? "Reporting" : "No recent telemetry"}
        />
      </div>

      <Card title="Recent anomalies">
        {alertState.loading && <LoadingState />}
        {alertState.error && (
          <ErrorState message={alertState.error} onRetry={alertState.reload} />
        )}
        {alertState.data && alerts.length === 0 && (
          <EmptyState
            title="No anomalies recorded"
            detail="Detected anomalies will appear here once the anomaly APIs are wired (Anomaly Center milestone)."
          />
        )}
        {alertState.data && alerts.length > 0 && (
          <ul className="event-list">
            {alerts.slice(0, 8).map((alert) => (
              <li key={alert.id} className="event-list__item">
                <SeverityPill severity={alert.severity} />
                <span className="event-list__message">{alert.message}</span>
                <span className="event-list__time">
                  {relativeTime(alert.first_seen_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="24-hour telemetry">
        <EmptyState
          title="Charts arrive with Live Monitoring"
          detail="The rolling 24-hour telemetry, cell-voltage and anomaly-count charts are embedded from Grafana in the Live Monitoring milestone."
        />
      </Card>
    </div>
  );
}
