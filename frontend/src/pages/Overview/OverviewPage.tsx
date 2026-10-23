/**
 * Overview dashboard (PRD §4.1).
 *
 * Operational summary: device connection status, last telemetry, anomaly counts
 * from the anomaly API (device-scoped), the latest predicted SoH, and an
 * embedded rolling 24-hour telemetry panel. No value is fabricated: a KPI with
 * no backend source shows an em-dash, never a placeholder number.
 */
import { Link } from "react-router-dom";

import { KpiCard, Card, PageHeader } from "@/components/cards/Cards";
import { EmptyState, ErrorState, LoadingState, StaleBadge } from "@/components/feedback/Feedback";
import { SeverityPill, StatusPill } from "@/components/feedback/Indicators";
import { GrafanaEmbed } from "@/components/charts/GrafanaEmbed";
import { deriveFreshness } from "@/hooks/useDataFreshness";
import { useAsync } from "@/hooks/useAsync";
import { usePolling } from "@/hooks/usePolling";
import { useSelectedDevice } from "@/hooks/useSelectedDevice";
import { listAnomalies } from "@/services/anomalies";
import { getDevice } from "@/services/devices";
import { getLatestPrediction } from "@/services/predictions";
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
  const alertState = usePolling(
    (signal) =>
      listAnomalies({ device_id: deviceId ?? undefined, limit: 50 }, signal),
    15_000,
    [deviceId],
  );
  const sohState = usePolling(
    (signal) =>
      deviceId ? getLatestPrediction(deviceId, signal) : Promise.resolve(null),
    30_000,
    [deviceId],
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

  const anomalies = alertState.data?.items ?? [];
  // "Active" excludes both terminal states; false_positive is a closed
  // outcome (the details page treats it as resolved), so counting it here
  // inflated the KPI and could light the critical banner for a non-issue.
  const activeAnomalies = anomalies.filter(
    (a) => a.status !== "resolved" && a.status !== "false_positive",
  );
  const criticalAnomalies = activeAnomalies.filter(
    (a) => a.severity === "critical",
  );
  const telemetry = telemetryState.data;
  const device = deviceState.data;
  const soh = sohState.data;
  // A malformed or unrelated payload must never crash the dashboard; only a
  // numeric prediction counts as a real SoH value.
  const sohValid =
    soh !== null && typeof soh.predicted_soh === "number";

  const freshness = deriveFreshness(telemetry, device);
  const connected = freshness.connected;

  return (
    <div className="page">
      <PageHeader
        title="Overview"
        subtitle={device ? `${device.name} · ${device.device_id}` : deviceId}
        actions={
          <>
            <span className="pill" title="Tested on NASA lab data and demo packs — not a hardware-proven BMS">
              PoC · Lab + demo data
            </span>
            <StaleBadge lastUpdated={telemetryState.lastUpdated ?? null} />
          </>
        }
      />

      {device && freshness.stale && (
        <div className="banner banner--warn" role="status">
          <strong>Telemetry stale.</strong> No sample for{" "}
          {Math.round(freshness.ageSeconds ?? 0)}s; the device was expected to
          report every{" "}
          {device.sampling_frequency_hz > 0
            ? `${(1 / device.sampling_frequency_hz).toFixed(1)}s`
            : "minute"}
          . The values below are the last received, not necessarily current.
        </div>
      )}

      {criticalAnomalies.length > 0 && (
        <div className="banner banner--critical" role="alert">
          <strong>
            {criticalAnomalies.length} active critical anomal
            {criticalAnomalies.length === 1 ? "y" : "ies"}.
          </strong>{" "}
          They remain listed until resolved.{" "}
          <Link to="/anomalies?severity=critical">Review in Anomaly Center →</Link>
        </div>
      )}

      <div className="kpi-grid">
        <KpiCard
          label="Active anomalies"
          value={activeAnomalies.length}
          tone={activeAnomalies.length > 0 ? "warn" : "ok"}
        />
        <KpiCard
          label="Critical anomalies"
          value={criticalAnomalies.length}
          tone={criticalAnomalies.length > 0 ? "danger" : "ok"}
        />
        <KpiCard
          label="Pack voltage"
          value={telemetry?.pack_voltage ?? "—"}
          unit="V"
          tone="info"
        />
        <KpiCard label="Current" value={telemetry?.current ?? "—"} unit="A" />
        <KpiCard
          label="Average temperature"
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
          label="Current SoH (predicted)"
          value={sohValid ? `${soh.predicted_soh.toFixed(1)}%` : "—"}
          hint={
            sohValid
              ? `measured ${typeof soh.measured_soh === "number" ? `${soh.measured_soh.toFixed(1)}%` : "n/a"} · ${relativeTime(soh.predicted_at)}`
              : "no prediction yet"
          }
          tone={sohValid ? "info" : "default"}
        />
        <KpiCard
          label="Device status"
          value={device ? <StatusPill status={device.status} /> : "—"}
          tone={connected ? "ok" : "warn"}
          hint={connected ? "Reporting" : "No recent telemetry"}
        />
      </div>

      <Card
        title="Recent anomalies"
        actions={
          <Link className="btn" to="/anomalies">
            Open Anomaly Center →
          </Link>
        }
      >
        {alertState.loading && !alertState.data && <LoadingState />}
        {alertState.error && (
          <ErrorState message={alertState.error} onRetry={alertState.refresh} />
        )}
        {alertState.data && anomalies.length === 0 && (
          <EmptyState
            title="No anomalies recorded"
            detail="Detected anomalies will appear here as the backend's detector opens episodes."
          />
        )}
        {alertState.data && anomalies.length > 0 && (
          <ul className="event-list">
            {anomalies.slice(0, 8).map((anomaly) => (
              <li key={anomaly.id} className="event-list__item">
                <SeverityPill severity={anomaly.severity} />
                <span className="event-list__message">
                  {anomaly.anomaly_type}
                  {anomaly.affected_cell !== null
                    ? ` · cell ${anomaly.affected_cell}`
                    : ""}
                </span>
                <span className="event-list__time">
                  {relativeTime(anomaly.detected_at)}
                </span>
                <Link className="btn btn--sm" to={`/anomalies/${anomaly.id}`}>
                  Investigate
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card
        title="Rolling 24-hour telemetry"
        actions={
          <Link className="btn" to="/live">
            Open Live Monitoring →
          </Link>
        }
      >
        {deviceId ? (
          <GrafanaEmbed variables={{ "var-device": deviceId }} height={360} />
        ) : (
          <EmptyState
            title="Select a device"
            detail="Choose a device to scope the rolling 24-hour telemetry window."
          />
        )}
      </Card>
    </div>
  );
}
