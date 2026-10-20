/**
 * Live Monitoring (PRD §4.2).
 *
 * Uses the two live sources the backend already provides: the SSE event stream
 * (one shared connection) and polled latest telemetry. It deliberately does not
 * poll at 1 Hz (PRD §6) — the stream pushes updates and a slow poll backfills.
 * Charts and Grafana embeds arrive in the Live Monitoring milestone.
 */
import { Card, KpiCard, PageHeader } from "@/components/cards/Cards";
import { EmptyState, StaleBadge } from "@/components/feedback/Feedback";
import { useStream } from "@/hooks/useEventStream";
import { usePolling } from "@/hooks/usePolling";
import { useSelectedDevice } from "@/hooks/useSelectedDevice";
import { getLatestTelemetry } from "@/services/telemetry";
import { relativeTime } from "@/utils/format";

export function LiveMonitoringPage() {
  const { deviceId } = useSelectedDevice();
  const { events, state } = useStream();
  const { data: telemetry, lastUpdated } = usePolling(
    (signal) =>
      deviceId ? getLatestTelemetry(deviceId, signal) : Promise.resolve(null),
    5_000,
    [deviceId],
  );

  return (
    <div className="page">
      <PageHeader
        title="Live Monitoring"
        subtitle={deviceId ?? "No device selected"}
        actions={<StaleBadge lastUpdated={lastUpdated} />}
      />

      <div className="kpi-grid">
        <KpiCard label="Stream" value={state} tone={state === "open" ? "ok" : "warn"} />
        <KpiCard label="Pack voltage" value={telemetry?.pack_voltage ?? "—"} unit="V" tone="info" />
        <KpiCard label="Current" value={telemetry?.current ?? "—"} unit="A" />
        <KpiCard label="Temperature" value={telemetry?.temperature ?? "—"} unit="°C" />
      </div>

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

      <Card title="Real-time charts">
        <EmptyState
          title="Charts arrive with the Live Monitoring milestone"
          detail="Cell-voltage, current and temperature charts (and the Grafana panel embeds) are added in this page's milestone."
        />
      </Card>
    </div>
  );
}
