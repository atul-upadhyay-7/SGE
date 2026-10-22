/**
 * Anomaly Center (PRD §4.3, §4.5).
 *
 * Lists detected anomalies with the summary counts, filters, an hourly activity
 * chart and a CSV export. Every number comes from the anomaly APIs — the page
 * never invents counts, and the time-bucketed activity uses persisted events
 * (PRD §4.5, §8) rather than resampled telemetry.
 */
import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { Card, KpiCard, PageHeader } from "@/components/cards/Cards";
import { StackedBarChart } from "@/components/charts/Charts";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  StaleBadge,
} from "@/components/feedback/Feedback";
import { SeverityPill, StatusPill } from "@/components/feedback/Indicators";
import { useAsync } from "@/hooks/useAsync";
import { usePolling } from "@/hooks/usePolling";
import { useSelectedDevice } from "@/hooks/useSelectedDevice";
import {
  getAnomalyHourly,
  getAnomalySummary,
  listAnomalies,
  type AnomalyQuery,
} from "@/services/anomalies";
import type { AnomalyEvent } from "@/types/domain";
import { downloadCsv, toCsv } from "@/utils/csv";
import {
  formatDateTime,
  formatDuration,
  formatHourBucket,
  fromDatetimeLocal,
} from "@/utils/format";

const PAGE_SIZE = 25;

type SortKey = "detected_at" | "severity" | "anomaly_type" | "duration_seconds";
type SortDir = "asc" | "desc";

const SEVERITY_ORDER: Record<string, number> = {
  info: 0,
  warning: 1,
  critical: 2,
};

interface Filters {
  severity: string;
  anomalyType: string;
  status: string;
  from: string;
  to: string;
}

const EMPTY_FILTERS: Filters = {
  severity: "",
  anomalyType: "",
  status: "",
  from: "",
  to: "",
};

const CSV_COLUMNS = [
  { key: "id", header: "Event ID" },
  { key: "detected_at", header: "Detected at (UTC)" },
  { key: "last_detected_at", header: "Last detected at (UTC)" },
  { key: "device_id", header: "Device" },
  { key: "anomaly_type", header: "Category" },
  { key: "severity", header: "Severity" },
  { key: "affected_cell", header: "Affected cell" },
  { key: "observed_value", header: "Observed" },
  { key: "expected_value", header: "Expected" },
  { key: "threshold", header: "Threshold" },
  { key: "duration_seconds", header: "Duration (s)" },
  { key: "status", header: "Status" },
];

export function AnomalyCenterPage() {
  const { deviceId } = useSelectedDevice();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [filters, setFilters] = useState<Filters>(() => ({
    ...EMPTY_FILTERS,
    severity: searchParams.get("severity") ?? "",
    anomalyType: searchParams.get("anomaly_type") ?? "",
    status: searchParams.get("status") ?? "",
  }));
  const [page, setPage] = useState(0);
  const [sortKey, setSortKey] = useState<SortKey>("detected_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const range = useMemo(
    () => ({
      from: fromDatetimeLocal(filters.from),
      to: fromDatetimeLocal(filters.to),
    }),
    [filters.from, filters.to],
  );

  const query: AnomalyQuery = {
    device_id: deviceId ?? undefined,
    severity: filters.severity || undefined,
    anomaly_type: filters.anomalyType || undefined,
    status: filters.status || undefined,
    from: range.from,
    to: range.to,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };

  const listState = usePolling(
    (signal) => listAnomalies(query, signal),
    15_000,
    [deviceId, filters.severity, filters.anomalyType, filters.status, range.from, range.to, page],
  );
  const summaryState = usePolling(
    (signal) =>
      getAnomalySummary(
        { device_id: deviceId ?? undefined, from: range.from, to: range.to },
        signal,
      ),
    15_000,
    [deviceId, range.from, range.to],
  );
  const hourlyState = useAsync(
    (signal) =>
      getAnomalyHourly(
        { device_id: deviceId ?? undefined, from: range.from, to: range.to },
        signal,
      ),
    [deviceId, range.from, range.to],
  );

  const total = listState.data?.total ?? 0;
  const rows = listState.data?.items ?? [];
  const sorted = useMemo(() => {
    const copy = [...rows];
    copy.sort((a, b) => {
      let cmp = 0;
      if (sortKey === "detected_at") {
        cmp = a.detected_at.localeCompare(b.detected_at);
      } else if (sortKey === "severity") {
        cmp =
          (SEVERITY_ORDER[String(a.severity)] ?? 0) -
          (SEVERITY_ORDER[String(b.severity)] ?? 0);
      } else if (sortKey === "anomaly_type") {
        cmp = a.anomaly_type.localeCompare(b.anomaly_type);
      } else {
        cmp = a.duration_seconds - b.duration_seconds;
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
    return copy;
  }, [rows, sortKey, sortDir]);

  const summary = summaryState.data;
  const buckets = hourlyState.data?.buckets ?? [];
  const last24h = summaryCount(buckets);

  function onSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  function handleExport() {
    const csv = toCsv(
      CSV_COLUMNS,
      sorted.map((row) => ({ ...row })),
    );
    downloadCsv(`anomalies-${new Date().toISOString().slice(0, 10)}.csv`, csv);
  }

  function applyFilters(next: Filters) {
    setFilters(next);
    setPage(0);
  }

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="page">
      <PageHeader
        title="Anomaly Center"
        subtitle={deviceId ? `Device ${deviceId}` : "All devices"}
        actions={
          <div className="page-header__actions">
            <StaleBadge lastUpdated={listState.lastUpdated} />
            <button
              type="button"
              className="btn"
              onClick={handleExport}
              disabled={sorted.length === 0}
            >
              Export CSV
            </button>
          </div>
        }
      />

      <div className="kpi-grid">
        <KpiCard label="Total anomalies" value={summary?.total ?? "—"} />
        <KpiCard
          label="Critical"
          value={severityCount(summary, "critical")}
          tone="danger"
        />
        <KpiCard
          label="Warnings"
          value={severityCount(summary, "warning")}
          tone="warn"
        />
        <KpiCard
          label="Unresolved"
          value={summary?.unresolved ?? "—"}
          tone={(summary?.unresolved ?? 0) > 0 ? "warn" : "ok"}
        />
        <KpiCard
          label="Repeated types"
          value={summary ? repeatCount(summary.by_type) : "—"}
          hint="types with more than one event"
        />
        <KpiCard label="Last 24 hours" value={last24h} hint="persisted events" />
      </div>

      <Card title="Filters">
        <form
          className="form form--grid"
          onSubmit={(event) => {
            event.preventDefault();
            applyFilters({ ...filters });
          }}
        >
          <label>
            Severity
            <select
              value={filters.severity}
              onChange={(e) => applyFilters({ ...filters, severity: e.target.value })}
            >
              <option value="">All</option>
              <option value="info">info</option>
              <option value="warning">warning</option>
              <option value="critical">critical</option>
            </select>
          </label>
          <label>
            Category
            <input
              value={filters.anomalyType}
              onChange={(e) =>
                applyFilters({ ...filters, anomalyType: e.target.value })
              }
              placeholder="e.g. cell_voltage_imbalance"
            />
          </label>
          <label>
            Status
            <select
              value={filters.status}
              onChange={(e) => applyFilters({ ...filters, status: e.target.value })}
            >
              <option value="">All</option>
              <option value="open">open</option>
              <option value="acknowledged">acknowledged</option>
              <option value="investigating">investigating</option>
              <option value="resolved">resolved</option>
                <option value="false_positive">false positive</option>
            </select>
          </label>
          <label>
            From (local time)
            <input
              type="datetime-local"
              value={filters.from}
              onChange={(e) => applyFilters({ ...filters, from: e.target.value })}
            />
          </label>
          <label>
            To (local time)
            <input
              type="datetime-local"
              value={filters.to}
              onChange={(e) => applyFilters({ ...filters, to: e.target.value })}
            />
          </label>
          <div className="form__actions" style={{ gap: 8 }}>
            <button type="submit" className="btn btn--primary">
              Apply
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => applyFilters(EMPTY_FILTERS)}
            >
              Clear
            </button>
          </div>
        </form>
      </Card>

      <Card title="Hourly anomaly activity">
        {hourlyState.loading && !hourlyState.data && <LoadingState />}
        {hourlyState.error && (
          <ErrorState message={hourlyState.error} onRetry={hourlyState.reload} />
        )}
        {hourlyState.data && (
          <>
            <StackedBarChart
              title="Anomalies per hour by severity"
              unit="events"
              labels={buckets.map((b) => formatHourBucket(b.hour))}
              series={[
                {
                  key: "critical",
                  label: "Critical",
                  color: "var(--critical)",
                  values: buckets.map((b) => b.by_severity?.critical ?? 0),
                },
                {
                  key: "warning",
                  label: "Warning",
                  color: "var(--warning)",
                  values: buckets.map((b) => b.by_severity?.warning ?? 0),
                },
                {
                  key: "info",
                  label: "Info",
                  color: "var(--info)",
                  values: buckets.map((b) => b.by_severity?.info ?? 0),
                },
              ]}
              emptyLabel="No anomalies in this period"
            />
            <p className="muted chart__note">
              Hours are shown in your local timezone; storage and aggregation are
              UTC (PRD §4.5).
            </p>
          </>
        )}
      </Card>

      <Card title={`Anomaly events (${total})`} className="card--wide">
        {listState.loading && !listState.data && <LoadingState />}
        {listState.error && (
          <ErrorState message={listState.error} onRetry={listState.refresh} />
        )}
        {listState.data && rows.length === 0 && (
          <EmptyState
            title="No anomalies match these filters"
            detail="Adjust the time range or filters, or clear them to see all detected anomalies."
          />
        )}
        {sorted.length > 0 && (
          <>
            <div className="table-wrap">
              <table className="table table--clickable">
                <thead>
                  <tr>
                    <th scope="col">Event ID</th>
                    <th
                      scope="col"
                      aria-sort={
                        sortKey === "detected_at"
                          ? sortDir === "asc"
                            ? "ascending"
                            : "descending"
                          : "none"
                      }
                    >
                      <SortHeader
                        label="Detected"
                        active={sortKey === "detected_at"}
                        dir={sortDir}
                        onClick={() => onSort("detected_at")}
                      />
                    </th>
                    <th scope="col">Device</th>
                    <th
                      scope="col"
                      aria-sort={
                        sortKey === "anomaly_type"
                          ? sortDir === "asc"
                            ? "ascending"
                            : "descending"
                          : "none"
                      }
                    >
                      <SortHeader
                        label="Category"
                        active={sortKey === "anomaly_type"}
                        dir={sortDir}
                        onClick={() => onSort("anomaly_type")}
                      />
                    </th>
                    <th
                      scope="col"
                      aria-sort={
                        sortKey === "severity"
                          ? sortDir === "asc"
                            ? "ascending"
                            : "descending"
                          : "none"
                      }
                    >
                      <SortHeader
                        label="Severity"
                        active={sortKey === "severity"}
                        dir={sortDir}
                        onClick={() => onSort("severity")}
                      />
                    </th>
                    <th scope="col">Cell</th>
                    <th scope="col">Observed</th>
                    <th scope="col">Expected</th>
                    <th
                      scope="col"
                      aria-sort={
                        sortKey === "duration_seconds"
                          ? sortDir === "asc"
                            ? "ascending"
                            : "descending"
                          : "none"
                      }
                    >
                      <SortHeader
                        label="Duration"
                        active={sortKey === "duration_seconds"}
                        dir={sortDir}
                        onClick={() => onSort("duration_seconds")}
                      />
                    </th>
                    <th scope="col">Status</th>
                    <th scope="col">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((event) => (
                    <AnomalyRow
                      key={event.id}
                      event={event}
                      onOpen={() => navigate(`/anomalies/${event.id}`)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
            <div className="pager">
              <button
                type="button"
                className="btn"
                disabled={page === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
              >
                ← Prev
              </button>
              <span className="muted">
                Page {page + 1} of {pageCount}
              </span>
              <button
                type="button"
                className="btn"
                disabled={page + 1 >= pageCount}
                onClick={() => setPage((p) => p + 1)}
              >
                Next →
              </button>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}

function SortHeader({
  label,
  active,
  dir,
  onClick,
}: {
  label: string;
  active: boolean;
  dir: SortDir;
  onClick: () => void;
}) {
  return (
    <button type="button" className="th-sort" onClick={onClick}>
      {label}
      {active && <span aria-hidden="true">{dir === "asc" ? " ▲" : " ▼"}</span>}
    </button>
  );
}

function AnomalyRow({
  event,
  onOpen,
}: {
  event: AnomalyEvent;
  onOpen: () => void;
}) {
  return (
    <tr
      className="table__row--clickable"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
    >
      <td className="mono">#{event.id}</td>
      <td title={formatDateTime(event.detected_at)}>
        {formatDateTime(event.detected_at)}
      </td>
      <td className="mono">{event.device_id}</td>
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
      <td>
        <Link
          className="btn btn--sm"
          to={`/anomalies/${event.id}`}
          onClick={(e) => e.stopPropagation()}
        >
          Investigate
        </Link>
      </td>
    </tr>
  );
}

function severityCount(
  summary: { by_severity: { severity: string; count: number }[] } | null,
  severity: string,
): number | string {
  if (!summary) return "—";
  return summary.by_severity.find((s) => s.severity === severity)?.count ?? 0;
}

function repeatCount(byType: { count: number }[]): number {
  return byType.filter((t) => t.count > 1).length;
}

function summaryCount(buckets: { total: number }[]): number | string {
  if (buckets.length === 0) return "—";
  return buckets.reduce((sum, b) => sum + b.total, 0);
}
