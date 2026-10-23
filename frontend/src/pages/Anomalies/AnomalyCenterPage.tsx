/**
 * Anomaly Center (PRD §4.3, §4.5).
 *
 * Lists detected anomalies with the summary counts, filters, an hourly activity
 * chart and a CSV export. Every number comes from the anomaly APIs — the page
 * never invents counts, and the time-bucketed activity uses persisted events
 * (PRD §4.5, §8) rather than resampled telemetry.
 */
import { useEffect, useMemo, useState } from "react";
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
  getAnomalyComparison,
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
  toDatetimeLocal,
} from "@/utils/format";

const PAGE_SIZE = 25;

type SortKey = "detected_at" | "severity" | "anomaly_type" | "duration_seconds";
type SortDir = "asc" | "desc";

interface Filters {
  severity: string;
  anomalyType: string;
  status: string;
  affectedCell: string;
  minRecurrence: string;
  from: string;
  to: string;
}

const EMPTY_FILTERS: Filters = {
  severity: "",
  anomalyType: "",
  status: "",
  affectedCell: "",
  minRecurrence: "1",
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
  // Draft vs applied filters: inputs edit the draft, and the
  // queries run off `applied`. Previously every keystroke wrote
  // straight into the query state, so typing a category fired a
  // request per character (each aborting the last) and reset the
  // page. The draft auto-applies after a short pause; Apply,
  // quick ranges and Clear commit immediately.
  const [initialFilters] = useState<Filters>(() => ({
    ...EMPTY_FILTERS,
    severity: searchParams.get("severity") ?? "",
    anomalyType: searchParams.get("anomaly_type") ?? "",
    status: searchParams.get("status") ?? "",
  }));
  const [draft, setDraft] = useState<Filters>(initialFilters);
  const [applied, setApplied] = useState<Filters>(initialFilters);
  const [page, setPage] = useState(0);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    if (filtersEqual(applied, draft)) return;
    const timer = setTimeout(() => {
      setApplied(draft);
      setPage(0);
    }, 400);
    return () => clearTimeout(timer);
  }, [draft, applied]);

  function applyNow(next: Filters) {
    setDraft(next);
    setApplied(next);
    setPage(0);
  }
  const [sortKey, setSortKey] = useState<SortKey>("detected_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const range = useMemo(
    () => ({
      from: fromDatetimeLocal(applied.from),
      to: fromDatetimeLocal(applied.to),
    }),
    [applied.from, applied.to],
  );

  const filterDeps = [
    deviceId,
    applied.severity,
    applied.anomalyType,
    applied.status,
    applied.affectedCell,
    applied.minRecurrence,
    range.from,
    range.to,
  ];

  const baseQuery: AnomalyQuery = {
    device_id: deviceId ?? undefined,
    severity: applied.severity || undefined,
    anomaly_type: applied.anomalyType || undefined,
    status: applied.status || undefined,
    affected_cell: applied.affectedCell === "" ? undefined : Number(applied.affectedCell),
    min_recurrence: Number(applied.minRecurrence),
    from: range.from,
    to: range.to,
  };

  // Sorting is server-side: the API orders before it pages, so the
  // order holds across the whole result set - a client-side sort
  // could only ever order the 25 rows on screen.
  const query: AnomalyQuery = {
    ...baseQuery,
    sort: sortKey,
    sort_dir: sortDir,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  };

  const listState = usePolling(
    (signal) => listAnomalies(query, signal),
    15_000,
    [...filterDeps, page, sortKey, sortDir],
  );
  const summaryState = usePolling(
    (signal) => getAnomalySummary({ ...baseQuery }, signal),
    15_000,
    filterDeps,
  );
  const hourlyState = useAsync(
    (signal) => getAnomalyHourly({ ...baseQuery }, signal),
    filterDeps,
  );

  const comparisonState = useAsync(
    signal => range.from && range.to ? getAnomalyComparison(baseQuery, signal) : Promise.resolve(null),
    filterDeps,
  );
  const total = listState.data?.total ?? 0;
  const rows = listState.data?.items ?? [];

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

  // Export means the filtered set, not the page on screen: walk
  // the paged API (bounded at 1,000 rows) with the same filters
  // and sort, then download.
  async function handleExport() {
    setExporting(true);
    try {
      const exportPageSize = 200;
      const maxRows = 1000;
      const all: AnomalyEvent[] = [];
      let offset = 0;
      let totalCount = Number.POSITIVE_INFINITY;
      while (all.length < Math.min(totalCount, maxRows)) {
        const pageData = await listAnomalies({
          ...baseQuery,
          sort: sortKey,
          sort_dir: sortDir,
          limit: exportPageSize,
          offset,
        });
        totalCount = pageData.total;
        all.push(...pageData.items);
        if (pageData.items.length < exportPageSize) break;
        offset += exportPageSize;
      }
      const csv = toCsv(
        CSV_COLUMNS,
        all.map((row) => ({ ...row })),
      );
      downloadCsv(`anomalies-${new Date().toISOString().slice(0, 10)}.csv`, csv);
    } catch {
      // A failed export simply produces no file; the list and its
      // own error state are unaffected.
    } finally {
      setExporting(false);
    }
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
              disabled={total === 0 || exporting}
            >
              {exporting ? "Exporting..." : "Export CSV"}
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
        <KpiCard label="Hourly activity" value={last24h} hint="filtered period; includes ongoing hours" />
      </div>

      <Card title="Filters">
        <div className="page-header__actions">
          {[1, 7, 30].map(days => <button key={days} type="button" className="btn" onClick={() => applyNow({...draft, from: toDatetimeLocal(new Date(Date.now()-days*86400000).toISOString()), to: toDatetimeLocal(new Date().toISOString())})}>{days === 1 ? '24h' : `${days}d`}</button>)}
        </div>
        <form
          className="form form--grid"
          onSubmit={(event) => {
            event.preventDefault();
            applyNow(draft);
          }}
        >
          <label>
            Severity
            <select
              value={draft.severity}
              onChange={(e) => setDraft({ ...draft, severity: e.target.value })}
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
              value={draft.anomalyType}
              onChange={(e) =>
                setDraft({ ...draft, anomalyType: e.target.value })
              }
              placeholder="e.g. cell_voltage_imbalance"
            />
          </label>
          <label>
            Status
            <select
              value={draft.status}
              onChange={(e) => setDraft({ ...draft, status: e.target.value })}
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
            Affected cell (0 = pack-level)
            <input type="number" min="0" value={draft.affectedCell} onChange={e => setDraft({...draft, affectedCell: e.target.value})} />
          </label>
          <label>
            Minimum recurrence in filtered period
            <input type="number" min="1" value={draft.minRecurrence} onChange={e => setDraft({...draft, minRecurrence: e.target.value || '1'})} />
          </label>
          <label>
            From (local time)
            <input
              type="datetime-local"
              value={draft.from}
              onChange={(e) => setDraft({ ...draft, from: e.target.value })}
            />
          </label>
          <label>
            To (local time)
            <input
              type="datetime-local"
              value={draft.to}
              onChange={(e) => setDraft({ ...draft, to: e.target.value })}
            />
          </label>
          <div className="form__actions" style={{ gap: 8 }}>
            <button type="submit" className="btn btn--primary">
              Apply
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => applyNow(EMPTY_FILTERS)}
            >
              Clear
            </button>
          </div>
        </form>
      </Card>

      <Card title="Previous-period comparison">
        {!range.from || !range.to ? <p className="muted">Choose 24h, 7d, 30d or a bounded custom period to compare with the preceding same-length period.</p> : null}
        {comparisonState.loading && <LoadingState />}
        {comparisonState.error && <ErrorState message={comparisonState.error} onRetry={comparisonState.reload} />}
        {comparisonState.data && <>
          <p>Current: {formatDateTime(comparisonState.data.current.from)} to {formatDateTime(comparisonState.data.current.to)}: {comparisonState.data.current.total} episodes.</p>
          <p>Previous: {formatDateTime(comparisonState.data.previous.from)} to {formatDateTime(comparisonState.data.previous.to)}: {comparisonState.data.previous.total} episodes.</p>
          <p>Change: {comparisonState.data.total_change > 0 ? '+' : ''}{comparisonState.data.total_change} ({comparisonState.data.total_change_percent === null ? 'no previous baseline' : `${comparisonState.data.total_change_percent.toFixed(1)}%`}).</p>
          <p className="muted">{comparisonState.data.note}</p>
        </>}
      </Card>
      <Card title="Category breakdown">
        {summary?.by_type.map(item => <p key={item.anomaly_type}>{item.anomaly_type}: {item.count}</p>)}
        <p className="muted">All counts, charts and recurrence use the same active filters. Recurrence means distinct episodes with the same device, category and cell in this filtered period.</p>
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
        {rows.length > 0 && (
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
                    <th scope="col">Recurrence</th>
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
                  {rows.map((event) => (
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
        // The row nests an Investigate link: keys aimed at the
        // link are the link's, not the row's.
        if (e.target !== e.currentTarget) return;
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
      <td>{event.recurrence_count ?? 1}</td>
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

function filtersEqual(a: Filters, b: Filters): boolean {
  return (
    a.severity === b.severity &&
    a.anomalyType === b.anomalyType &&
    a.status === b.status &&
    a.affectedCell === b.affectedCell &&
    a.minRecurrence === b.minRecurrence &&
    a.from === b.from &&
    a.to === b.to
  );
}
