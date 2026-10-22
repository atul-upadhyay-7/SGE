/**
 * Anomaly Details and Investigation (PRD §4.4).
 *
 * Shows the identification, the measured vs expected values, a telemetry trace
 * around the detection window, the diagnosis (with confirmed causes kept apart
 * from unconfirmed ones) and the resolution workflow. Causes and recommended
 * actions come from the backend's versioned rule set — the page never invents a
 * diagnosis or a recommendation (PRD §4.4, §10).
 */
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Card, KpiCard, PageHeader } from "@/components/cards/Cards";
import { LineChart } from "@/components/charts/Charts";
import { ConfirmButton } from "@/components/feedback/ConfirmButton";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  StaleBadge,
} from "@/components/feedback/Feedback";
import { SeverityPill, StatusPill } from "@/components/feedback/Indicators";
import { useAsync } from "@/hooks/useAsync";
import {
  acknowledgeAnomaly,
  addAnomalyAction,
  getAnomaly,
  getAnomalyHistory,
  resolveAnomaly,
} from "@/services/anomalies";
import { getTelemetryHistory } from "@/services/telemetry";
import type {
  AnomalyAction,
  AnomalyCause,
  AnomalyEvent,
  AnomalyRecommendation,
} from "@/types/domain";
import {
  formatDateTime,
  formatDuration,
  relativeTime,
} from "@/utils/format";

const TRACE_WINDOW_MS = 10 * 60 * 1000;

export function AnomalyDetailsPage() {
  const { anomalyId } = useParams<{ anomalyId: string }>();
  const id = Number(anomalyId);
  const valid = Number.isFinite(id);

  const eventState = useAsync(
    (signal) => (valid ? getAnomaly(id, signal) : Promise.resolve(null)),
    [id, valid],
  );
  const historyState = useAsync(
    (signal) => (valid ? getAnomalyHistory(id, signal) : Promise.resolve([])),
    [id, valid],
  );

  const event = eventState.data;
  const history = historyState.data ?? [];

  const [lastLoaded, setLastLoaded] = useState<Date | null>(null);
  useEffect(() => {
    if (event) setLastLoaded(new Date());
  }, [event]);

  const traceState = useAsync(
    (signal) =>
      event
        ? getTelemetryHistory(
            event.device_id,
            {
              from: new Date(
                new Date(event.detected_at).getTime() - TRACE_WINDOW_MS,
              ).toISOString(),
              to: new Date(
                new Date(event.last_detected_at).getTime() + TRACE_WINDOW_MS,
              ).toISOString(),
              order: "asc",
              limit: 200,
            },
            signal,
          )
        : Promise.resolve(null),
    [event?.id, event?.device_id],
  );

  function reloadAll() {
    eventState.reload();
    historyState.reload();
  }

  if (!valid) {
    return (
      <div className="page">
        <PageHeader title="Anomaly Details" />
        <Card>
          <EmptyState title="Invalid anomaly id" detail={anomalyId} />
        </Card>
      </div>
    );
  }

  return (
    <div className="page">
      <PageHeader
        title={event ? `Anomaly #${event.id}` : `Anomaly #${anomalyId}`}
        subtitle={event ? `${event.device_id} · ${event.anomaly_type}` : undefined}
        actions={
          <div className="page-header__actions">
            <StaleBadge lastUpdated={lastLoaded} />
            <Link className="btn" to="/anomalies">
              ← Back to Anomaly Center
            </Link>
          </div>
        }
      />

      {eventState.loading && !event && <LoadingState />}
      {eventState.error && (
        <ErrorState message={eventState.error} onRetry={eventState.reload} />
      )}

      {event && (
        <>
          <Identification event={event} />

          <Card title="Measurements">
            <div className="kpi-grid">
              <KpiCard
                label="Observed value"
                value={event.observed_value ?? "—"}
              />
              <KpiCard
                label="Expected value"
                value={event.expected_value ?? "—"}
              />
              <KpiCard label="Threshold" value={event.threshold ?? "—"} />
              <KpiCard
                label="Deviation"
                value={deviation(event)}
                tone="warn"
              />
            </div>
          </Card>

          <Card title="Telemetry around the event">
            {traceState.loading && !traceState.data && <LoadingState />}
            {traceState.error && (
              <ErrorState
                message={traceState.error}
                onRetry={traceState.reload}
              />
            )}
            {traceState.data && (
              <TelemetryTrace
                samples={traceState.data.items}
                detectedAt={event.detected_at}
                affectedCell={event.affected_cell}
              />
            )}
          </Card>

          <Diagnosis event={event} />

          <CorrectiveActions actions={event.recommended_actions} />

          <ResolutionWorkflow
            event={event}
            history={history}
            historyLoading={historyState.loading}
            historyError={historyState.error}
            onChanged={reloadAll}
          />
        </>
      )}
    </div>
  );
}

function Identification({ event }: { event: AnomalyEvent }) {
  return (
    <Card title="Identification">
      <dl className="detail-grid">
        <Detail label="Event ID" value={`#${event.id}`} />
        <Detail label="Anomaly type" value={event.anomaly_type} />
        <Detail
          label="Severity"
          value={<SeverityPill severity={event.severity} />}
        />
        <Detail label="Device" value={event.device_id} mono />
        <Detail
          label="Affected cell"
          value={event.affected_cell ?? "—"}
        />
        <Detail
          label="First detected"
          value={formatDateTime(event.detected_at)}
        />
        <Detail
          label="Last detected"
          value={formatDateTime(event.last_detected_at)}
        />
        <Detail
          label="Duration"
          value={formatDuration(event.duration_seconds)}
        />
        <Detail label="Status" value={<StatusPill status={event.status} />} />
        <Detail label="Rule version" value={event.rule_version} mono />
      </dl>
      <p className="muted detail-note">
        All timestamps are UTC from the backend; shown in your local timezone
        (PRD §7).
      </p>
    </Card>
  );
}

function Detail({
  label,
  value,
  mono,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="detail">
      <dt className="detail__label">{label}</dt>
      <dd className={`detail__value ${mono ? "mono" : ""}`}>{value}</dd>
    </div>
  );
}

function deviation(event: AnomalyEvent): string {
  if (event.observed_value === null || event.expected_value === null) return "—";
  const delta = event.observed_value - event.expected_value;
  const sign = delta > 0 ? "+" : "";
  return `${sign}${delta.toFixed(3)}`;
}

function TelemetryTrace({
  samples,
  detectedAt,
  affectedCell,
}: {
  samples: { measured_at: string; pack_voltage: number | null; current: number; temperature: number; cell_voltages: number[] }[];
  detectedAt: string;
  affectedCell: number | null;
}) {
  const detectedMs = new Date(detectedAt).getTime();
  const base = detectedMs || 0;

  const voltageSeries = useMemo(
    () => [
      {
        key: "pack_voltage",
        label: "Pack voltage (V)",
        color: "var(--info)",
        points: samples
          .filter((s) => s.pack_voltage !== null)
          .map((s) => ({
            x: new Date(s.measured_at).getTime() - base,
            y: s.pack_voltage as number,
          })),
      },
    ],
    [samples, base],
  );

  const cellSeries = useMemo(() => {
    if (affectedCell === null) return [];
    const index = affectedCell - 1;
    return [
      {
        key: "affected_cell",
        label: `Cell ${affectedCell} voltage (V)`,
        color: "var(--critical)",
        points: samples
          .filter((s) => s.cell_voltages[index] !== undefined)
          .map((s) => ({
            x: new Date(s.measured_at).getTime() - base,
            y: s.cell_voltages[index],
          })),
      },
    ];
  }, [samples, affectedCell, base]);

  if (samples.length === 0) {
    return (
      <EmptyState
        title="No telemetry stored for this window"
        detail="The event's detection window has no persisted samples to plot."
      />
    );
  }

  return (
    <div className="grid grid--2">
      <LineChart
        title="Pack voltage around detection (t=0 at detection)"
        series={voltageSeries}
        unit="V"
        formatY={(v) => v.toFixed(1)}
        formatX={(v) => `${Math.round(v / 60000)}m`}
        xLabel="Minutes from detection"
        emptyLabel="No pack voltage in window"
      />
      {cellSeries.length > 0 && (
        <LineChart
          title={`Affected cell voltage around detection`}
          series={cellSeries}
          unit="V"
          formatY={(v) => v.toFixed(3)}
          formatX={(v) => `${Math.round(v / 60000)}m`}
          xLabel="Minutes from detection"
        />
      )}
    </div>
  );
}

function Diagnosis({ event }: { event: AnomalyEvent }) {
  const causes = event.likely_causes ?? [];
  const confirmed = causes.filter((c) => c.confirmed === true);
  const unconfirmed = causes.filter((c) => c.confirmed !== true);

  return (
    <Card title="Diagnosis">
      <div className="banner banner--info" role="note">
        <span aria-hidden="true">i</span>
        <span>
          Likely causes are drawn from the backend rule set and are{" "}
          <strong>not</strong> confirmed component failures. Confirm against
          physical inspection before acting (PRD §4.4).
        </span>
      </div>

      {causes.length === 0 && (
        <EmptyState
          title="No causes recorded"
          detail="The detector did not attach likely causes to this episode."
        />
      )}

      {confirmed.length > 0 && (
        <div className="cause-group">
          <h3 className="cause-group__title">Supported by evidence</h3>
          <CauseList causes={confirmed} confirmed />
        </div>
      )}
      {unconfirmed.length > 0 && (
        <div className="cause-group">
          <h3 className="cause-group__title">Possible explanations (unconfirmed)</h3>
          <CauseList causes={unconfirmed} confirmed={false} />
        </div>
      )}

      {event.diagnostic_evidence && (
        <details className="evidence">
          <summary>Supporting evidence (raw)</summary>
          <pre className="code">
            {JSON.stringify(event.diagnostic_evidence, null, 2)}
          </pre>
        </details>
      )}
    </Card>
  );
}

function CauseList({
  causes,
  confirmed,
}: {
  causes: (AnomalyCause | string)[];
  confirmed: boolean;
}) {
  const items = causes.map((entry) =>
    typeof entry === "string" ? { cause: entry } : entry,
  );
  return (
    <ul className="cause-list">
      {items.map((cause, index) => (
        <li key={`${cause.cause}-${index}`} className="cause-list__item">
          <span className={`tag ${confirmed ? "tag--ok" : "tag--muted"}`}>
            {confirmed ? "evidence-backed" : "unconfirmed"}
          </span>
          <span>{cause.cause}</span>
          {typeof cause.evidence === "string" && cause.evidence && (
            <span className="muted cause-list__evidence">{cause.evidence}</span>
          )}
        </li>
      ))}
    </ul>
  );
}

function CorrectiveActions({
  actions,
}: {
  actions: (AnomalyRecommendation | string)[] | null;
}) {
  const list = (actions ?? []).map((entry) =>
    typeof entry === "string"
      ? { action: entry, priority: null as string | null }
      : entry,
  );
  return (
    <Card title="Recommended corrective actions">
      {list.length === 0 ? (
        <EmptyState
          title="No recommendations attached"
          detail="The rule set did not provide corrective actions for this anomaly type."
        />
      ) : (
        <ol className="action-list">
          {list.map((action, index) => (
            <li key={`${action.action}-${index}`} className="action-list__item">
              <div className="action-list__body">
                <span className="action-list__text">{action.action}</span>
                <span className="action-list__meta">
                  {action.priority && <span className="tag">{action.priority}</span>}
                  {action.category && <span className="tag">{action.category}</span>}
                </span>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function ResolutionWorkflow({
  event,
  history,
  historyLoading,
  historyError,
  onChanged,
}: {
  event: AnomalyEvent;
  history: AnomalyAction[];
  historyLoading: boolean;
  historyError: string | null;
  onChanged: () => void;
}) {
  const [actor, setActor] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const resolved = ["resolved", "false_positive"].includes(event.status);
  const [classification, setClassification] = useState<"resolved" | "false_positive">("resolved");
  const [outcome, setOutcome] = useState("");

  async function run(fn: () => Promise<unknown>, success: string) {
    setBusy(true);
    setMessage(null);
    try {
      await fn();
      setMessage(success);
      setNote("");
      onChanged();
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Resolution workflow">
      {resolved && (
        <div className="banner banner--ok" role="status">
          <span aria-hidden="true">✓</span>
          <span>
            {event.status === "false_positive" ? "False positive" : "Resolved"}{event.ended_at ? ` ${relativeTime(event.ended_at)}` : ""}.
          </span>
        </div>
      )}

      {event.resolution_outcome && <p>Resolution outcome: {event.resolution_outcome}</p>}
      <div className="workflow">
        <label className="workflow__field">
          Final classification
          <select value={classification} onChange={(e) => setClassification(e.target.value as "resolved" | "false_positive")} disabled={resolved}>
            <option value="resolved">Resolved</option>
            <option value="false_positive">False positive</option>
          </select>
        </label>
        <label className="workflow__field workflow__field--wide">
          Resolution outcome / false-positive explanation
          <textarea value={outcome} onChange={(e) => setOutcome(e.target.value)} disabled={resolved} maxLength={2048} />
        </label>
        <label className="workflow__field">
          Investigator (actor)
          <input
            value={actor}
            onChange={(e) => setActor(e.target.value)}
            placeholder="your name"
          />
        </label>
        <label className="workflow__field workflow__field--wide">
          Note / evidence
          <textarea
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="What did you observe or do?"
          />
        </label>
        <div className="workflow__actions">
          <button
            type="button"
            className="btn"
            disabled={busy || resolved || event.status !== "open"}
            onClick={() =>
              run(
                () => acknowledgeAnomaly(event.id, { actor: actor || null, note: note || null }),
                "Anomaly acknowledged",
              )
            }
          >
            Acknowledge
          </button>
          <button
            type="button"
            className="btn"
            disabled={busy || resolved}
            onClick={() =>
              run(
                () =>
                  addAnomalyAction(event.id, {
                    action_type: "note",
                    actor: actor || null,
                    note: note || null,
                  }),
                "Note recorded",
              )
            }
          >
            Add note
          </button>
          <button
            type="button"
            className="btn"
            disabled={busy || resolved || note.trim().length === 0}
            title="Requires a note describing the action taken"
            onClick={() =>
              run(
                () =>
                  addAnomalyAction(event.id, {
                    action_type: "corrective_action",
                    actor: actor || null,
                    note,
                  }),
                "Corrective action recorded",
              )
            }
          >
            Record corrective action
          </button>
          <ConfirmButton
            className="btn btn--primary"
            label={classification === "false_positive" ? "Mark false positive" : "Mark resolved"}
            confirmLabel={classification === "false_positive" ? "Confirm false positive" : "Confirm resolve"}
            disabled={busy || resolved || (classification === "false_positive" && !outcome.trim())}
            title="Resolving is final for this event; confirming prevents an accidental click"
            onConfirm={() =>
              run(
                () => resolveAnomaly(event.id, { actor: actor || null, note: note || null, classification, outcome: outcome || null }),
                "Anomaly resolved",
              )
            }
          />
        </div>
        {message && <p className="form__message">{message}</p>}
      </div>

      <h3 className="cause-group__title">Investigation log</h3>
      {historyLoading && <LoadingState />}
      {historyError && <ErrorState message={historyError} />}
      {!historyLoading && !historyError && history.length === 0 && (
        <EmptyState
          title="No actions yet"
          detail="Acknowledgements, notes and the resolution will appear here."
        />
      )}
      {history.length > 0 && (
        <ol className="timeline">
          {history.map((action) => (
            <li key={action.id} className="timeline__item">
              <span className={`timeline__dot timeline__dot--${action.action_type}`} />
              <div className="timeline__body">
                <div className="timeline__head">
                  <span className="tag">{action.action_type}</span>
                  <span className="muted">
                    {action.actor ? `${action.actor} · ` : ""}
                    {formatDateTime(action.created_at)}
                  </span>
                </div>
                {action.note && <p className="timeline__note">{action.note}</p>}
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}
