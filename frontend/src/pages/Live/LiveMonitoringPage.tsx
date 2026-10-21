/**
 * Live Monitoring (PRD §4.2).
 *
 * The rolling 24-hour telemetry graphs live in Grafana and are embedded here as
 * the primary panel; this page supplies the surrounding live context the PRD
 * asks for — latest measurement and data freshness, device connection status
 * (distinct from backend health), and the SSE event stream the backend pushes.
 *
 * It does not poll at 1 Hz (PRD §6): the stream carries live updates and a slow
 * poll backfills the latest sample for the freshness readout.
 */
import { Card, KpiCard, PageHeader } from "@/components/cards/Cards";
import { EmptyState, StaleBadge } from "@/components/feedback/Feedback";
import { GrafanaEmbed } from "@/components/charts/GrafanaEmbed";
import { deriveFreshness } from "@/hooks/useDataFreshness";
import { useStream } from "@/hooks/useEventStream";
import { usePolling } from "@/hooks/usePolling";
import { useSelectedDevice } from "@/hooks/useSelectedDevice";
import { useAsync } from "@/hooks/useAsync";
import { getDevice } from "@/services/devices";
import { getLatestTelemetry } from "@/services/telemetry";
import { formatDateTime, relativeTime } from "@/utils/format";
import { config } from "@/config";

function secondsLabel(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  return `${Math.round(minutes / 60)} h`;
}

export function LiveMonitoringPage() {
  const { deviceId } = useSelectedDevice();
  const { events, state, lastEventAt } = useStream();
  const deviceState = useAsync(
    (signal) => (deviceId ? getDevice(deviceId, signal) : Promise.resolve(null)),
    [deviceId],
  );
  const { data: telemetry, lastUpdated } = usePolling(
    (signal) =>
      deviceId ? getLatestTelemetry(deviceId, signal) : Promise.resolve(null),
    5_000,
    [deviceId],
  );

  const freshness = deriveFreshness(telemetry, deviceState.data);

  return (
    <div className="page">
      <PageHeader
        title="Live Monitoring"
        subtitle={deviceId ?? "No device selected"}
        actions={<StaleBadge lastUpdated={lastUpdated} />}
      />

      {freshness.stale && (
        <div className="banner banner--warn" role="status">
          <span aria-hidden="true">!</span>
          <span>
            Telemetry is stale — the newest sample is{" "}
            {secondsLabel(freshness.ageSeconds)} old. The device may have stopped
            reporting; the API can be healthy while the BMS is silent (PRD §4.9).
          </span>
        </div>
      )}

      <div className="kpi-grid">
        <KpiCard
          label="Device connection"
          value={freshness.connected ? "Reporting" : "Not reporting"}
          tone={freshness.connected ? "ok" : "warn"}
          hint={
            freshness.measuredAtMs
              ? `last sample ${relativeTime(telemetry?.measured_at)}`
              : "no telemetry stored"
          }
        />
        <KpiCard
          label="Latest measurement"
          value={
            freshness.measuredAtMs ? relativeTime(telemetry?.measured_at) : "—"
          }
          hint={
            telemetry ? formatDateTime(telemetry.measured_at) : "awaiting data"
          }
          tone={freshness.stale ? "warn" : "default"}
        />
        <KpiCard
          label="Data freshness"
          value={secondsLabel(freshness.ageSeconds)}
          hint="age of newest sample"
          tone={freshness.connected ? "ok" : "warn"}
        />
        <KpiCard
          label="Stream"
          value={state}
          tone={state === "open" ? "ok" : "warn"}
          hint={lastEventAt ? `last event ${relativeTime(lastEventAt)}` : "no events"}
        />
      </div>

      <Card
        title="Rolling 24-hour telemetry"
        actions={
          <span className="muted">
            Grafana · {config.grafanaUrl}
          </span>
        }
      >
        {deviceId ? (
          <GrafanaEmbed variables={{ "var-device": deviceId }} height={520} />
        ) : (
          <EmptyState
            title="Select a device"
            detail="Choose a device to scope the rolling 24-hour telemetry window."
          />
        )}
        <p className="muted grafana-embed__note">
          The window is fixed at <span className="mono">now-24h</span> to{" "}
          <span className="mono">now</span> with automatic refresh: new telemetry
          enters from the right, readings older than 24 hours leave on the left,
          and missing telemetry appears as a gap (never a fabricated line).
        </p>
      </Card>

      <Card title="Live event stream">
        {events.length === 0 ? (
          <EmptyState
            title="Waiting for events"
            detail="Telemetry, cycle, alert and monitor events will appear here as the backend publishes them."
          />
        ) : (
          <ul className="event-list">
            {events.map((event, index) => (
              <li key={`${event.at}-${index}`} className="event-list__item">
                <span className={`kind kind--${event.kind}`}>{event.kind}</span>
                <span className="event-list__message mono">
                  {JSON.stringify(event.data)}
                </span>
                <span className="event-list__time">{relativeTime(event.at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
