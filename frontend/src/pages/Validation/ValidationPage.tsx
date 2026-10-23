/**
 * Model Validation — PoC validation slice.
 *
 * Shows the latest run of scripts/validate_real_data.py, which scores
 * discharge cycles through the live estimator (XGBoost
 * ``soh_window_all`` only). ElasticNet appears here, if at all, as a
 * clearly labelled offline benchmark — it is not deployed and is not
 * a live improvement.
 *
 * No report = no metrics: a 404 renders the empty state with the
 * command that produces a report, never placeholder numbers.
 */
import { useMemo } from "react";

import { Card, KpiCard, PageHeader } from "@/components/cards/Cards";
import { LineChart } from "@/components/charts/Charts";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/feedback/Feedback";
import { useAsync } from "@/hooks/useAsync";
import { getValidationSummary } from "@/services/validation";
import type { ValidationReport, ValidationRow } from "@/types/domain";
import { formatDateTime } from "@/utils/format";

const TABLE_LIMIT = 25;

export function ValidationPage() {
  const state = useAsync((signal) => getValidationSummary(signal), []);
  const report = state.data;
  const noReport = state.errorStatus === 404;

  return (
    <div className="page">
      <PageHeader
        title="Model Validation"
        subtitle="Live XGBoost model scored against measured SoH"
        actions={
          <span className="pill pill--info">
            PoC — not hardware-proven
          </span>
        }
      />

      {state.loading && !report && <LoadingState />}

      {!state.loading && noReport && (
        <Card>
          <EmptyState
            title="No validation run yet"
            detail={
              "No real-sensor validation has been recorded. Run: " +
              "python scripts/validate_real_data.py --input <cycles.csv> " +
              "--source real_sensor — then reload this page. Metrics are " +
              "never shown until a real run exists."
            }
          />
        </Card>
      )}

      {!state.loading && state.error && !noReport && (
        <Card>
          <ErrorState
            message={state.error ?? "Could not load the validation report"}
            onRetry={state.reload}
          />
        </Card>
      )}

      {report && <ValidationReportView report={report} />}
    </div>
  );
}

function ValidationReportView({ report }: { report: ValidationReport }) {
  const { summary } = report;
  const scored = useMemo(
    () => report.rows.filter((r) => r.status === "scored"),
    [report.rows],
  );
  const latest = useMemo(
    () => [...report.rows].slice(-TABLE_LIMIT).reverse(),
    [report.rows],
  );
  const isLabSample = report.source !== "real_sensor";

  return (
    <>
      <p className="muted detail-note">
        Source: <span className="pill">{report.source}</span>{" "}
        Dataset: <span className="mono">{report.dataset}</span> · Run:{" "}
        {formatDateTime(report.generated_at)} · Live model:{" "}
        <strong>XGBoost</strong> (<span className="mono">{report.model.name}</span>,{" "}
        <span className="mono">{report.model.artifact}</span>)
      </p>

      {isLabSample && (
        <div className="banner banner--warn" role="status">
          <strong>Lab-sample validation — real sensor validation pending.</strong>{" "}
          This run uses lab data, not a real sensor export. It verifies the
          validation harness and pipeline; it is not held-out accuracy and
          not hardware validation.
        </div>
      )}

      <div className="kpi-grid">
        <KpiCard
          label="Live model"
          value="XGBoost"
          hint={report.model.name}
          tone="info"
        />
        <KpiCard label="Cycles scored" value={summary.scored_rows} />
        <KpiCard
          label="Cycles refused"
          value={summary.refused_rows}
          tone={summary.refused_rows > 0 ? "warn" : "ok"}
          hint="refused, never guessed"
        />
        <KpiCard
          label="MAE"
          value={summary.mae_pct === null ? "—" : `${summary.mae_pct.toFixed(2)}%`}
          hint="predicted vs measured SoH"
        />
        <KpiCard
          label="RMSE"
          value={summary.rmse_pct === null ? "—" : `${summary.rmse_pct.toFixed(2)}%`}
        />
        <KpiCard
          label="Bias"
          value={summary.bias_pct === null ? "—" : `${summary.bias_pct.toFixed(2)}%`}
          hint="mean predicted − measured"
        />
      </div>

      <Card title="Predicted vs measured SoH">
        <PredictedVsActual rows={scored} />
      </Card>

      <Card title={`Latest cycles (${latest.length} of ${report.rows.length})`}>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Cell</th>
                <th>Discharge</th>
                <th>Measured SoH</th>
                <th>Predicted SoH</th>
                <th>Error</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {latest.map((row) => (
                <tr key={`${row.cell_id}-${row.discharge_index}`}>
                  <td className="mono">{row.cell_id}</td>
                  <td>{row.discharge_index}</td>
                  <td>{row.actual_soh_pct.toFixed(2)}%</td>
                  <td>
                    {row.predicted_soh_pct === null
                      ? "—"
                      : `${row.predicted_soh_pct.toFixed(2)}%`}
                  </td>
                  <td>
                    {row.error_pct === null ? "—" : `${row.error_pct.toFixed(2)}%`}
                  </td>
                  <td>
                    {row.status === "refused"
                      ? `refused (${row.refusal_reason ?? "refused"})`
                      : row.status}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {report.offline_benchmark && (
        <Card title="Offline benchmark — not live">
          <p>
            <span className="pill pill--warning">
              Offline benchmark only — not live, not deployed
            </span>
          </p>
          <p className="muted detail-note">
            Held-out {report.offline_benchmark.cell_id ?? "cell"} from{" "}
            <span className="mono">{report.offline_benchmark.source_file}</span>{" "}
            ({report.offline_benchmark.rows} discharges): XGBoost MAE{" "}
            {report.offline_benchmark.xgboost_mae_pct.toFixed(2)}%, ElasticNet
            MAE {report.offline_benchmark.elasticnet_mae_pct.toFixed(2)}%.
            ElasticNet is compared offline only — it does not produce live
            predictions and is not a live improvement.
          </p>
        </Card>
      )}

      <Card title="Scope & honesty notes">
        <ul>
          {report.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
          <li>
            PoC — validated on lab data so far; real sensor validation
            pending. This is not a hardware-proven BMS.
          </li>
        </ul>
      </Card>
    </>
  );
}

function PredictedVsActual({ rows }: { rows: ValidationRow[] }) {
  const series = useMemo(() => {
    const ordered = [...rows].sort(
      (a, b) =>
        a.cell_id.localeCompare(b.cell_id) ||
        a.discharge_index - b.discharge_index,
    );
    return [
      {
        key: "actual",
        label: "Measured SoH (%)",
        color: "var(--normal)",
        points: ordered.map((r, i) => ({ x: i + 1, y: r.actual_soh_pct })),
      },
      {
        key: "predicted",
        label: "Predicted SoH (%)",
        color: "var(--info)",
        dashed: true,
        points: ordered.map((r, i) => ({
          x: i + 1,
          y: r.predicted_soh_pct as number,
        })),
      },
    ];
  }, [rows]);

  return (
    <LineChart
      title="Predicted vs measured SoH per scored cycle"
      series={series}
      unit="%"
      formatY={(v) => `${v.toFixed(1)}`}
      formatX={(v) => `${Math.round(v)}`}
      xLabel="Scored cycle (in cell / discharge order)"
      emptyLabel="No scored cycles in this validation run"
    />
  );
}
