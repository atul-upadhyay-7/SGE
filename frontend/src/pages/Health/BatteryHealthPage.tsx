/**
 * Battery Health — SoH (PRD §4.6).
 *
 * Shows the latest prediction and its history, the model metadata, and the
 * input-quality status. Predicted and measured values are labelled separately,
 * and when the model needs cycle-level features that do not exist yet the page
 * shows an explicit pending state rather than a fabricated score (PRD §4.6, §10).
 */
import { useMemo, useState } from "react";

import { Card, KpiCard, PageHeader } from "@/components/cards/Cards";
import { LineChart } from "@/components/charts/Charts";
import type { LineSeries } from "@/components/charts/Charts";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  StaleBadge,
} from "@/components/feedback/Feedback";
import { useAsync } from "@/hooks/useAsync";
import { usePolling } from "@/hooks/usePolling";
import { useSelectedDevice } from "@/hooks/useSelectedDevice";
import {
  getActiveModels,
  getLatestPrediction,
  getPredictionHistory,
  getRul,
} from "@/services/predictions";
import type { RulEstimate, SohPrediction } from "@/types/domain";
import {
  formatAxisTime,
  formatDateTime,
  formatPercent,
  localZoneName,
  relativeTime,
} from "@/utils/format";

/** Input-quality states that mean "no trustworthy prediction yet". */
const PENDING_QUALITY = new Set(["insufficient_data", "refused", "pending"]);

export function BatteryHealthPage() {
  const { deviceId } = useSelectedDevice();
  const [rangeKey, setRangeKey] = useState<"24h" | "7d" | "30d" | "all">("all");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [viewMode, setViewMode] = useState<"time" | "cycle">("time");

  const from = rangeStart(rangeKey);

  const latestState = usePolling(
    (signal) =>
      deviceId ? getLatestPrediction(deviceId, signal) : Promise.resolve(null),
    30_000,
    [deviceId],
  );
  const rulState = usePolling(
    (signal) =>
      deviceId ? getRul(deviceId, signal) : Promise.resolve(null),
    30_000,
    [deviceId],
  );
  const historyState = usePolling(
    (signal) =>
      deviceId
        ? getPredictionHistory(
            deviceId,
            { from, order, limit: 500 },
            signal,
          )
        : Promise.resolve(null),
    30_000,
    [deviceId, from, order],
  );
  const modelsState = useAsync(
    (signal) => getActiveModels(signal),
    [],
  );

  const latest = latestState.data;
  const rul = rulState.data;
  const history = historyState.data?.items ?? [];

  if (!deviceId) {
    return (
      <div className="page">
        <PageHeader title="Battery Health (SoH)" />
        <Card>
          <EmptyState
            title="Select a device"
            detail="Choose a device to see its state-of-health prediction and history."
          />
        </Card>
      </div>
    );
  }

  // Only a 404 means "no prediction yet". Any other failure (500,
  // network) is an error with a retry — the old code conflated the
  // two by treating every error as pending, and branched on the
  // rendered "Not found" message string to tell them apart.
  const failed = latestState.error !== null && latestState.errorStatus !== 404;
  const pending =
    latestState.errorStatus === 404 ||
    (latest ? PENDING_QUALITY.has(latest.input_quality_status) : false);

  return (
    <div className="page">
      <PageHeader
        title="Battery Health (SoH)"
        subtitle={`Device ${deviceId}`}
        actions={<StaleBadge lastUpdated={latestState.lastUpdated} />}
      />

      {latestState.loading && !latest && <LoadingState />}

      {!latestState.loading && failed && (
        <Card>
          <ErrorState
            message={latestState.error ?? "Could not load the latest prediction"}
            onRetry={latestState.refresh}
          />
        </Card>
      )}

      {!latestState.loading && !failed && pending && (
        <Card>
          <EmptyState
            title="No SoH prediction available yet"
            detail={
              latest
                ? `The latest prediction's input data was "${latest.input_quality_status}". SoH is only produced once sufficient cycle-level features exist; it is never inferred from live telemetry (PRD §4.6).`
                : "No stored prediction yet. Complete and aggregate enough discharge windows to form the model reference baseline."
            }
          />
          {latest && <PredictionMeta prediction={latest} />}
        </Card>
      )}

      {latest && !pending && (
        <div className="kpi-grid">
          <KpiCard
            label="Predicted SoH"
            value={formatPercent(latest.predicted_soh)}
            tone="info"
            hint={`predicted ${relativeTime(latest.predicted_at)}`}
          />
          <KpiCard
            label="Measured SoH"
            value={formatPercent(latest.measured_soh)}
            hint={
              latest.measured_soh === null
                ? "not available for this cycle"
                : "from capacity measurement"
            }
          />
          <KpiCard
            label="Difference"
            value={deltaLabel(latest)}
            tone="default"
            hint="predicted − measured"
          />
          <KpiCard
            label="Health Status"
            value={healthStatus(latest.predicted_soh).label}
            tone={healthStatus(latest.predicted_soh).tone}
            hint={healthStatus(latest.predicted_soh).hint}
          />
          <KpiCard
            label="Estimated RUL"
            value={rulLabel(rul)}
            tone={rulTone(rul)}
            hint={rulHint(rul)}
          />
          <KpiCard label="Model version" value={latest.model_version} hint="active artifact" />
          <KpiCard
            label="Input quality"
            value={latest.input_quality_status}
            tone={qualityTone(latest.input_quality_status)}
          />
          <KpiCard
            label="Prediction time"
            value={relativeTime(latest.predicted_at)}
            hint={formatDateTime(latest.predicted_at)}
          />
        </div>
      )}

      <Card
        title="SoH history"
        actions={
          <div className="page-header__actions">
            <label className="chart-select">
              View
              <select
                value={viewMode}
                onChange={(e) => setViewMode(e.target.value as "time" | "cycle")}
              >
                <option value="time">Time (Date)</option>
                <option value="cycle">Cycle (#)</option>
              </select>
            </label>
            <label className="chart-select">
              Range
              <select
                value={rangeKey}
                onChange={(e) =>
                  setRangeKey(e.target.value as typeof rangeKey)
                }
              >
                <option value="24h">24 hours</option>
                <option value="7d">7 days</option>
                <option value="30d">30 days</option>
                <option value="all">All</option>
              </select>
            </label>
            <label className="chart-select">
              Order
              <select
                value={order}
                onChange={(e) => setOrder(e.target.value as "asc" | "desc")}
              >
                <option value="desc">Newest first</option>
                <option value="asc">Oldest first</option>
              </select>
            </label>
          </div>
        }
      >
        {historyState.loading && !historyState.data && <LoadingState />}
        {historyState.error && (
          <ErrorState
            message={historyState.error}
            onRetry={historyState.refresh}
          />
        )}
        {historyState.data && history.length === 0 && (
          <EmptyState
            title="No prediction history in this range"
            detail="Widen the range, or aggregate charge cycles to generate predictions."
          />
        )}
        {history.length > 0 && (
          <SohHistoryChart items={history} viewMode={viewMode} />
        )}
      </Card>

      <Card title="Model & feature metadata">
        {modelsState.loading && !modelsState.data && <LoadingState />}
        {modelsState.error && (
          <ErrorState message={modelsState.error} onRetry={modelsState.reload} />
        )}
        {modelsState.data && modelsState.data.items.length === 0 && (
          <EmptyState
            title="No active model"
            detail="The model registry reports no active artifact. Predictions require an activated model."
          />
        )}
        {modelsState.data && modelsState.data.items.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Version</th>
                  <th>Feature schema</th>
                  <th>Training dataset</th>
                  <th>Active</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {modelsState.data.items.map((model) => (
                  <tr key={`${model.model_name}-${model.model_version}`}>
                    <td className="mono">{model.model_name}</td>
                    <td className="mono">{model.model_version}</td>
                    <td className="mono">{model.feature_schema_version}</td>
                    <td>{model.training_dataset_version ?? "—"}</td>
                    <td>{model.is_active ? "yes" : "no"}</td>
                    <td title={formatDateTime(model.created_at)}>
                      {relativeTime(model.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {modelsState.data && modelsState.data.items.length > 0 && (
          <p className="detail-note">
            <span className="pill">Live model: XGBoost</span>{" "}
            <span className="mono">
              {modelsState.data.items.map((m) => m.model_name).join(", ")}
            </span>{" "}
            is the only model used for live prediction. ElasticNet is an
            offline benchmark only — not deployed, not a live improvement.
          </p>
        )}
        {latest && (
          <>
            <p className="muted detail-note">
              Latest prediction used model <span className="mono">{latest.model_version}</span>{" "}
              with feature schema{" "}
              <span className="mono">{latest.feature_schema_version}</span>.
              {latest.cycle_id !== null && (
                <>
                  {" "}
                  Feature-window reference: cycle{" "}
                  <span className="mono">#{latest.cycle_id}</span>.
                </>
              )}
            </p>
            <p className="muted detail-note">
              No confidence interval is shown: the active model does not publish a
              validated uncertainty estimate, and the UI must not invent one
              (PRD §4.6).
            </p>
          </>
        )}
      </Card>
    </div>
  );
}

function PredictionMeta({ prediction }: { prediction: SohPrediction }) {
  return (
    <dl className="detail-grid">
      <div className="detail">
        <dt className="detail__label">Model version</dt>
        <dd className="detail__value mono">{prediction.model_version}</dd>
      </div>
      <div className="detail">
        <dt className="detail__label">Feature schema</dt>
        <dd className="detail__value mono">{prediction.feature_schema_version}</dd>
      </div>
      <div className="detail">
        <dt className="detail__label">Input quality</dt>
        <dd className="detail__value">{prediction.input_quality_status}</dd>
      </div>
      <div className="detail">
        <dt className="detail__label">Predicted at</dt>
        <dd className="detail__value">{formatDateTime(prediction.predicted_at)}</dd>
      </div>
    </dl>
  );
}

function SohHistoryChart({
  items,
  viewMode,
}: {
  items: SohPrediction[];
  viewMode: "time" | "cycle";
}) {
  const ordered = useMemo(() => {
    return [...items].sort((a, b) => {
      if (viewMode === "cycle" && a.cycle_id !== null && b.cycle_id !== null) {
        return a.cycle_id - b.cycle_id;
      }
      return a.predicted_at.localeCompare(b.predicted_at);
    });
  }, [items, viewMode]);

  const { series, yMin, formatX, xLabel } = useMemo(() => {
    const isCycle = viewMode === "cycle";

    const getX = (item: SohPrediction, index: number) => {
      if (isCycle) {
        return item.cycle_id ?? index + 1;
      }
      return new Date(item.predicted_at).getTime();
    };

    const predicted = ordered.map((item, index) => ({
      x: getX(item, index),
      y: item.predicted_soh,
    }));

    const measured = ordered
      .filter((item) => item.measured_soh !== null)
      .map((item, index) => ({
        x: getX(item, index),
        y: item.measured_soh as number,
      }));

    const allPoints = [...predicted, ...measured];
    const xs = allPoints.map((p) => p.x);
    const ys = allPoints.map((p) => p.y);
    const minX = xs.length ? Math.min(...xs) : 0;
    const maxX = xs.length ? Math.max(...xs) : 1;
    const lowestY = ys.length ? Math.min(...ys) : 80;
    // Set dynamic range minimum so the degradation curve has distinct vertical span
    const calculatedMinY = Math.min(75, Math.floor(lowestY / 5) * 5);

    const out: LineSeries[] = [
      {
        key: "predicted",
        label: "Predicted SoH (%)",
        color: "var(--info)",
        points: predicted,
      },
    ];

    if (measured.length > 0) {
      out.push({
        key: "measured",
        label: "Measured SoH (%)",
        color: "var(--normal)",
        points: measured,
        dashed: true,
      });
    }

    // 80% EOL reference threshold line
    const thresholdMinX = minX === maxX ? minX - 1 : minX;
    const thresholdMaxX = minX === maxX ? maxX + 1 : maxX;
    out.push({
      key: "threshold-eol",
      label: "EOL Threshold (80%)",
      color: "var(--critical)",
      points: [
        { x: thresholdMinX, y: 80 },
        { x: thresholdMaxX, y: 80 },
      ],
      dashed: true,
    });

    let xFormatter: (v: number) => string;
    let label: string;

    if (isCycle) {
      xFormatter = (v) => `#${Math.round(v)}`;
      label = "Cycle Number (#)";
    } else {
      const spanMs = maxX - minX;
      if (spanMs > 24 * 3600 * 1000) {
        xFormatter = (v) => {
          const d = new Date(v);
          return Number.isNaN(d.getTime())
            ? ""
            : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
        };
      } else {
        xFormatter = formatAxisTime;
      }
      label = `Time (${localZoneName()})`;
    }

    return {
      series: out,
      yMin: Math.max(0, calculatedMinY),
      formatX: xFormatter,
      xLabel: label,
    };
  }, [ordered, viewMode]);

  return (
    <LineChart
      title={
        viewMode === "cycle"
          ? "SoH Degradation Curve by Cycle Number"
          : "Predicted vs Measured SoH Over Time"
      }
      series={series}
      unit="%"
      formatY={(v) => `${v.toFixed(1)}`}
      formatX={formatX}
      xLabel={xLabel}
      yMin={yMin}
      yMax={100}
      emptyLabel="No SoH history"
    />
  );
}

function healthStatus(soh: number): {
  label: string;
  tone: "ok" | "warn" | "danger" | "default";
  hint: string;
} {
  if (soh >= 90) {
    return {
      label: "Healthy",
      tone: "ok",
      hint: "Nominal capacity (>90%)",
    };
  }
  if (soh >= 80) {
    return {
      label: "Aging",
      tone: "warn",
      hint: "Acceptable degradation (80–90%)",
    };
  }
  return {
    label: "Degraded (EOL)",
    tone: "danger",
    hint: "Below 80% EOL threshold",
  };
}

function rulLabel(rul: RulEstimate | null | undefined): string {
  if (!rul) return "—";
  if (rul.status === "target_reached") return "EOL Reached";
  if (rul.rul_discharges !== null && rul.rul_discharges !== undefined) {
    return `~${Math.round(rul.rul_discharges)} cycles`;
  }
  if (rul.status === "insufficient_history") return "Evaluating...";
  return "—";
}

function rulTone(rul: RulEstimate | null | undefined): "ok" | "warn" | "danger" | "default" {
  if (!rul || rul.rul_discharges === null || rul.rul_discharges === undefined) return "default";
  if (rul.rul_discharges > 30) return "ok";
  if (rul.rul_discharges > 10) return "warn";
  return "danger";
}

function rulHint(rul: RulEstimate | null | undefined): string {
  if (!rul) return "Remaining useful life to EOL";
  if (rul.status === "target_reached") return "Pack has reached 80% capacity limit";
  if (rul.p10_discharges !== null && rul.p90_discharges !== null) {
    return `P10–P90: ${Math.round(rul.p10_discharges)}–${Math.round(rul.p90_discharges)} cycles to ${rul.threshold_soh_pct}% EOL`;
  }
  return `To ${rul.threshold_soh_pct}% nominal capacity`;
}

function rangeStart(rangeKey: "24h" | "7d" | "30d" | "all"): string | undefined {
  if (rangeKey === "all") return undefined;
  const hours = rangeKey === "24h" ? 24 : rangeKey === "7d" ? 24 * 7 : 24 * 30;
  return new Date(Date.now() - hours * 3600 * 1000).toISOString();
}

function qualityTone(status: string): "ok" | "warn" | "danger" | "default" {
  if (PENDING_QUALITY.has(status)) return "warn";
  if (status === "ok" || status === "good" || status === "valid") return "ok";
  return "default";
}

function deltaLabel(prediction: SohPrediction): string {
  if (prediction.measured_soh === null) return "—";
  const delta = prediction.predicted_soh - prediction.measured_soh;
  const sign = delta > 0 ? "+" : "";
  return `${sign}${delta.toFixed(1)}%`;
}
