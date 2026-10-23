/**
 * Historical Reports (PRD §4.7).
 *
 * Generate a report from filters + section toggles, browse stored reports, read
 * a rendered snapshot, and export JSON/CSV. Generation is synchronous on the
 * backend; every value shown comes from the stored snapshot, never recomputed
 * or invented in the client (PRD §4.7, §10).
 */
import { useEffect, useState } from "react";

import { Card, KpiCard, PageHeader } from "@/components/cards/Cards";
import { StackedBarChart } from "@/components/charts/Charts";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  StaleBadge,
} from "@/components/feedback/Feedback";
import { SeverityPill, StatusPill } from "@/components/feedback/Indicators";
import { usePolling } from "@/hooks/usePolling";
import { useSelectedDevice } from "@/hooks/useSelectedDevice";
import {
  createReport,
  downloadReport,
  getReport,
  listReports,
  saveBlob,
} from "@/services/reports";
import type {
  CountMap,
  ReportCreate,
  ReportDetail,
  ReportEventRow,
  ReportPayload,
  ReportSections,
} from "@/types/domain";
import {
  formatDateTime,
  formatDuration,
  formatHourBucket,
  fromDatetimeLocal,
  relativeTime,
} from "@/utils/format";

const DEFAULT_SECTIONS: ReportSections = {
  telemetry: true,
  soh: true,
  anomalies: true,
  actions: true,
};

interface FormState {
  deviceId: string;
  from: string;
  to: string;
  severity: string;
  anomalyType: string;
  sections: ReportSections;
  generatedBy: string;
  queueInBackground: boolean;
}

export function ReportsPage() {
  const { deviceId: selectedDevice } = useSelectedDevice();
  const [form, setForm] = useState<FormState>({
    deviceId: selectedDevice ?? "",
    from: "",
    to: "",
    severity: "",
    anomalyType: "",
    sections: DEFAULT_SECTIONS,
    generatedBy: "",
    queueInBackground: false,
  });
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  // Poll quickly while a queued report is generating, then settle back.
  const [hasPending, setHasPending] = useState(false);
  const listState = usePolling(
    (signal) => listReports({ limit: 50 }, signal),
    hasPending ? 3_000 : 20_000,
  );
  const reports = listState.data?.items ?? [];
  useEffect(() => {
    setHasPending(
      reports.some(
        (report) => report.status === "queued" || report.status === "running",
      ),
    );
  }, [reports]);

  const detailState = usePolling(
    (signal) =>
      selectedId !== null ? getReport(selectedId, signal) : Promise.resolve(null),
    30_000,
    [selectedId],
  );

  async function handleGenerate(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const payload: ReportCreate = {
        device_id: form.deviceId || null,
        from: fromDatetimeLocal(form.from) ?? null,
        to: fromDatetimeLocal(form.to) ?? null,
        severity: form.severity || null,
        anomaly_type: form.anomalyType || null,
        sections: form.sections,
        generated_by: form.generatedBy || null,
        queued: form.queueInBackground || undefined,
      };
      const created = await createReport(payload);
      setSelectedId(created.id);
      setMessage(
        created.status === "queued"
          ? `Report #${created.id} queued; the list updates automatically when it is ready.`
          : `Report #${created.id} generated (${created.row_count} anomaly rows).`,
      );
      listState.refresh();
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "Generation failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleDownload(id: number, format: "json" | "csv" | "pdf") {
    setMessage(null);
    try {
      const { blob, filename } = await downloadReport(id, format);
      saveBlob(blob, filename);
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "Download failed");
    }
  }

  return (
    <div className="page">
      <PageHeader
        title="Historical Reports"
        subtitle="Generate, review and export reproducible operational reports"
        actions={<StaleBadge lastUpdated={listState.lastUpdated} />}
      />

      <Card title="Generate a report">
        <form className="form form--grid" onSubmit={handleGenerate}>
          <label>
            Device
            <input
              value={form.deviceId}
              onChange={(e) => setForm({ ...form, deviceId: e.target.value })}
              placeholder="blank = all devices"
            />
          </label>
          <label>
            From (local time)
            <input
              type="datetime-local"
              value={form.from}
              onChange={(e) => setForm({ ...form, from: e.target.value })}
            />
          </label>
          <label>
            To (local time)
            <input
              type="datetime-local"
              value={form.to}
              onChange={(e) => setForm({ ...form, to: e.target.value })}
            />
          </label>
          <label>
            Severity
            <select
              value={form.severity}
              onChange={(e) => setForm({ ...form, severity: e.target.value })}
            >
              <option value="">All</option>
              <option value="info">info</option>
              <option value="warning">warning</option>
              <option value="critical">critical</option>
            </select>
          </label>
          <label>
            Anomaly category
            <input
              value={form.anomalyType}
              onChange={(e) => setForm({ ...form, anomalyType: e.target.value })}
              placeholder="e.g. cell_voltage_out_of_range"
            />
          </label>
          <label>
            Generated by
            <input
              value={form.generatedBy}
              onChange={(e) => setForm({ ...form, generatedBy: e.target.value })}
              placeholder="optional"
            />
          </label>

          <fieldset className="form__sections">
            <legend>Sections to include</legend>
            {(
              ["telemetry", "soh", "anomalies", "actions"] as const
            ).map((key) => (
              <label key={key} className="checkbox">
                <input
                  type="checkbox"
                  checked={form.sections[key]}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      sections: { ...form.sections, [key]: e.target.checked },
                    })
                  }
                />
                {key}
              </label>
            ))}
          </fieldset>

          <label className="checkbox">
            <input
              type="checkbox"
              checked={form.queueInBackground}
              onChange={(e) =>
                setForm({ ...form, queueInBackground: e.target.checked })
              }
            />
            Queue in background (for large reports)
          </label>

          <div className="form__actions">
            <button type="submit" className="btn btn--primary" disabled={busy}>
              {busy
                ? form.queueInBackground
                  ? "Queueing…"
                  : "Generating…"
                : form.queueInBackground
                  ? "Queue report"
                  : "Generate report"}
            </button>
          </div>
        </form>
        <p className="muted detail-note">
          Report type: <span className="mono">full</span> — one complete report
          whose sections you can switch off. JSON, CSV and PDF exports are available from the same stored snapshot.
        </p>
        {message && <p className="form__message">{message}</p>}
      </Card>

      <Card title={`Stored reports (${listState.data?.total ?? 0})`} className="card--wide">
        {listState.loading && !listState.data && <LoadingState />}
        {listState.error && (
          <ErrorState message={listState.error} onRetry={listState.refresh} />
        )}
        {listState.data && reports.length === 0 && (
          <EmptyState
            title="No reports yet"
            detail="Generate a report above; it will be stored and listed here."
          />
        )}
        {reports.length > 0 && (
          <div className="table-wrap">
            <table className="table table--clickable">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Device</th>
                  <th>Period</th>
                  <th>Status</th>
                  <th>Rows</th>
                  <th>Format</th>
                  <th>Created</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {reports.map((report) => (
                  <tr
                    key={report.id}
                    className={`table__row--clickable ${
                      selectedId === report.id ? "table__row--selected" : ""
                    }`}
                    tabIndex={0}
                    onClick={() => setSelectedId(report.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setSelectedId(report.id);
                      }
                    }}
                  >
                    <td className="mono">#{report.id}</td>
                    <td className="mono">{report.device_id ?? "all devices"}</td>
                    <td title={periodLabel(report.period_start, report.period_end)}>
                      {periodLabel(report.period_start, report.period_end)}
                    </td>
                    <td>
                      <StatusPill status={report.status} />
                    </td>
                    <td>{report.row_count}</td>
                    <td className="mono">{report.format_version}</td>
                    <td title={formatDateTime(report.created_at)}>
                      {relativeTime(report.created_at)}
                    </td>
                    <td>
                      <div className="row-actions">
                        <button
                          type="button"
                          className="btn btn--sm"
                          disabled={report.status !== "ready"}
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDownload(report.id, "json");
                          }}
                        >
                          JSON
                        </button>
                        <button
                          type="button"
                          className="btn btn--sm"
                          disabled={report.status !== "ready"}
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDownload(report.id, "csv");
                          }}
                        >
                          CSV
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {selectedId !== null && (
        <Card
          title={`Report #${selectedId}`}
          actions={
            <div className="page-header__actions">
              <button
                type="button"
                className="btn"
                disabled={detailState.data?.status !== "ready"}
                onClick={() => handleDownload(selectedId, "json")}
              >
                Download JSON
              </button>
              <button
                type="button"
                className="btn"
                disabled={detailState.data?.status !== "ready"}
                onClick={() => handleDownload(selectedId, "csv")}
              >
                Download CSV
              </button>
              <button type="button" className="btn" disabled={detailState.data?.status !== "ready"} onClick={() => handleDownload(selectedId, "pdf")}>Download PDF</button>
            </div>
          }
        >
          {detailState.loading && !detailState.data && <LoadingState />}
          {detailState.error && (
            <ErrorState
              message={detailState.error}
              onRetry={detailState.refresh}
            />
          )}
          {detailState.data &&
          (detailState.data.status === "queued" ||
            detailState.data.status === "running") ? (
            <EmptyState
              title={
                detailState.data.status === "queued"
                  ? "Report is queued"
                  : "Report is generating"
              }
              detail="The background worker is building this report; this view updates automatically when it is ready."
            />
          ) : detailState.data?.payload?.summary ? (
            <ReportView report={detailState.data} payload={detailState.data.payload} />
          ) : (
            detailState.data && (
              <EmptyState
                title="Report has no stored payload"
                detail="This report snapshot is empty."
              />
            )
          )}
        </Card>
      )}
    </div>
  );
}

function periodLabel(
  from: string | null,
  to: string | null,
): string {
  if (!from && !to) return "all time";
  const f = from ? formatDateTime(from) : "start";
  const t = to ? formatDateTime(to) : "now";
  return `${f} → ${t}`;
}

function ReportView({
  report,
  payload,
}: {
  report: ReportDetail;
  payload: ReportPayload;
}) {
  const summary = payload.summary;
  return (
    <div className="report">
      <div className="kpi-grid">
        <KpiCard
          label="Samples in period"
          value={summary.samples ?? "—"}
          hint={payload.data_availability.scope}
        />
        <KpiCard
          label="Anomalies"
          value={summary.anomalies_total}
          tone={summary.anomalies_total > 0 ? "warn" : "ok"}
        />
        <KpiCard
          label="Unresolved"
          value={summary.anomalies_unresolved}
          tone={summary.anomalies_unresolved > 0 ? "danger" : "ok"}
        />
        <KpiCard
          label="Latest SoH"
          value={
            summary.latest_predicted_soh === null
              ? "—"
              : `${summary.latest_predicted_soh.toFixed(1)}%`
          }
          tone="info"
        />
      </div>

      <section className="report__block">
        <h3 className="report__heading">Report details</h3>
        <dl className="detail-grid">
          <Detail label="Report ID" value={`#${report.id}`} mono />
          <Detail label="Format version" value={payload.format_version} mono />
          <Detail label="Generated at" value={formatDateTime(payload.generated_at)} />
          <Detail label="Generated by" value={report.generated_by ?? "—"} />
          <Detail label="Device" value={payload.device.device_id ?? "all devices (fleet)"} mono />
          <Detail label="Scope" value={payload.device.scope} />
          <Detail
            label="Period"
            value={periodLabel(payload.period.from, payload.period.to)}
          />
          <Detail label="Sections" value={payload.included_sections.join(", ")} />
        </dl>
      </section>

      <section className="report__block">
        <h3 className="report__heading">Data availability</h3>
        {(
          <dl className="detail-grid">
            <Detail
              label="Samples"
              value={payload.data_availability.sample_count ?? 0}
            />
            <Detail
              label="First sample"
              value={
                payload.data_availability.first_measured_at
                  ? formatDateTime(payload.data_availability.first_measured_at)
                  : "—"
              }
            />
            <Detail
              label="Last sample"
              value={
                payload.data_availability.last_measured_at
                  ? formatDateTime(payload.data_availability.last_measured_at)
                  : "—"
              }
            />
            <Detail
              label="Rejected messages"
              value={payload.data_availability.rejection_count ?? 0}
            />
          </dl>
        )}
        {payload.data_availability.gaps && <p>Observed internal gaps: {payload.data_availability.gaps.count}; longest {formatDuration(payload.data_availability.gaps.longest_gap_seconds)}. {payload.data_availability.gaps.definition}</p>}
        <p className="muted">{payload.data_availability.rejection_time_basis === 'received_at' ? 'Rejections are counted by receipt time inside this report period, not payload measurement time.' : 'Legacy snapshot: rejection totals may be all-time.'}</p>
      </section>

      {payload.telemetry && (
        <section className="report__block">
          <h3 className="report__heading">Telemetry summary</h3>
          {payload.telemetry.aggregation_note && <p className="muted">{payload.telemetry.aggregation_note}</p>}
          <StatTable
            rows={[
              { label: "Pack voltage (V)", stats: payload.telemetry.pack_voltage },
              { label: "Current (A)", stats: payload.telemetry.current },
              { label: "Temperature (°C)", stats: payload.telemetry.temperature },
            ]}
          />
        </section>
      )}

      {payload.soh && (
        <section className="report__block">
          <h3 className="report__heading">SoH prediction history</h3>
          {payload.soh.fleet_latest_mean_soh != null && <p>Mean latest SoH per device in period: {payload.soh.fleet_latest_mean_soh.toFixed(2)}% ({payload.soh.devices_with_predictions} devices).</p>}
          {payload.soh.count === 0 ? (
            <EmptyState
              title="No SoH predictions in this period"
              detail="Predictions require aggregated charge cycles."
            />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Device</th>
                    <th>Predicted at</th>
                    <th>Predicted %</th>
                    <th>Measured %</th>
                    <th>Model</th>
                    <th>Quality</th>
                  </tr>
                </thead>
                <tbody>
                  {payload.soh.history.map((row, index) => (
                    <tr key={`${row.predicted_at}-${index}`}>
                      <td>{row.device_id ?? payload.device.device_id ?? 'Legacy snapshot'}</td>
                      <td>{formatDateTime(row.predicted_at)}</td>
                      <td>{row.predicted_soh.toFixed(1)}</td>
                      <td>{row.measured_soh === null ? "—" : row.measured_soh.toFixed(1)}</td>
                      <td className="mono">{row.model_version}</td>
                      <td>{row.input_quality_status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {payload.anomalies && (
        <section className="report__block">
          <h3 className="report__heading">Anomaly breakdown</h3>
          <div className="grid grid--2">
            <CountList title="By severity" counts={payload.anomalies.by_severity} />
            <CountList title="By category" counts={payload.anomalies.by_type} />
          </div>
          {payload.anomalies.hourly.length > 0 && (
            <StackedBarChart
              title="Anomalies per hour by severity"
              unit="events"
              labels={payload.anomalies.hourly.map((b) => formatHourBucket(b.hour))}
              series={[
                {
                  key: "critical",
                  label: "Critical",
                  color: "var(--critical)",
                  values: payload.anomalies.hourly.map((b) => b.by_severity?.critical ?? 0),
                },
                {
                  key: "warning",
                  label: "Warning",
                  color: "var(--warning)",
                  values: payload.anomalies.hourly.map((b) => b.by_severity?.warning ?? 0),
                },
                {
                  key: "info",
                  label: "Info",
                  color: "var(--info)",
                  values: payload.anomalies.hourly.map((b) => b.by_severity?.info ?? 0),
                },
              ]}
              emptyLabel="No anomalies in this period"
            />
          )}
        </section>
      )}

      {payload.anomalies && payload.anomalies.events.length > 0 && (
        <section className="report__block">
          <h3 className="report__heading">
            Anomaly events ({payload.anomalies.events.length})
          </h3>
          <EventTable rows={payload.anomalies.events} />
        </section>
      )}

      {payload.actions && (
        <section className="report__block">
          <h3 className="report__heading">
            Recorded actions ({payload.actions.count})
          </h3>
          {payload.actions.count === 0 ? (
            <EmptyState
              title="No actions recorded"
              detail="No acknowledgements, notes or resolutions in this period."
            />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Anomaly</th>
                    <th>Category</th>
                    <th>Action</th>
                    <th>Actor</th>
                    <th>Note</th>
                    <th>At</th>
                  </tr>
                </thead>
                <tbody>
                  {payload.actions.items.map((action, index) => (
                    <tr key={`${action.anomaly_id}-${index}`}>
                      <td className="mono">#{action.anomaly_id}</td>
                      <td>{action.anomaly_type}</td>
                      <td>{action.action_type}</td>
                      <td>{action.actor ?? "—"}</td>
                      <td>{action.note ?? "—"}</td>
                      <td>{formatDateTime(action.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {payload.anomalies && payload.anomalies.unresolved.length > 0 && (
        <section className="report__block">
          <h3 className="report__heading">
            Unresolved anomalies ({payload.anomalies.unresolved_count})
          </h3>
          <EventTable rows={payload.anomalies.unresolved} />
        </section>
      )}

      <section className="report__block">
        <h3 className="report__heading">Model & report metadata</h3>
        <dl className="detail-grid">
          <Detail label="Rules version" value={payload.model_metadata.rules_version} mono />
          <Detail
            label="Active models"
            value={
              payload.model_metadata.active_models.length === 0
                ? "none"
                : payload.model_metadata.active_models
                    .map((m) => `${m.model_name}@${m.model_version}`)
                    .join(", ")
            }
            mono
          />
        </dl>
      </section>
    </div>
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

function StatTable({
  rows,
}: {
  rows: { label: string; stats: Record<string, number | null> }[];
}) {
  const keys = ["min", "max", "mean"];
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Metric</th>
            {keys.map((k) => (
              <th key={k}>{k}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <td>{row.label}</td>
              {keys.map((k) => (
                <td key={k}>{row.stats?.[k] ?? "—"}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function normaliseCounts(counts: CountMap): [string, number][] {
  if (Array.isArray(counts)) return counts;
  return Object.entries(counts);
}

function CountList({
  title,
  counts,
}: {
  title: string;
  counts: CountMap;
}) {
  const entries = normaliseCounts(counts);
  return (
    <div className="count-list">
      <h4 className="count-list__title">{title}</h4>
      {entries.length === 0 ? (
        <p className="muted">none</p>
      ) : (
        <ul className="count-list__items">
          {entries.map(([name, count]) => (
            <li key={name} className="count-list__item">
              <span>{name}</span>
              <span className="mono">{count}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function EventTable({ rows }: { rows: ReportEventRow[] }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Detected</th>
            <th>Category</th>
            <th>Severity</th>
            <th>Cell</th>
            <th>Observed</th>
            <th>Expected</th>
            <th>Duration</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((event) => (
            <tr key={event.id}>
              <td className="mono">#{event.id}</td>
              <td>{formatDateTime(event.detected_at)}</td>
              <td>{event.anomaly_type}</td>
              <td>
                <SeverityPill severity={event.severity} />
              </td>
              <td>{event.affected_cell ?? "—"}</td>
              <td>{event.observed_value ?? "—"}</td>
              <td>{event.expected_value ?? event.threshold ?? "—"}</td>
              <td>{formatDuration(event.duration_seconds)}</td>
              <td>
                <StatusPill status={event.status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
