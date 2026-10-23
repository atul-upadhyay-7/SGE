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
} from "@/services/predictions";
import type { SohPrediction } from "@/types/domain";
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

  const from = rangeStart(rangeKey);

  const latestState = usePolling(
    (signal) =>
      deviceId ? getLatestPrediction(deviceId, signal) : Promise.resolve(null),
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
        {history.length > 0 && <SohHistoryChart items={history} />}
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

function SohHistoryChart({ items }: { items: SohPrediction[] }) {
  const ordered = useMemo(
    () => [...items].sort((a, b) => a.predicted_at.localeCompare(b.predicted_at)),
    [items],
  );

  const series = useMemo(() => {
    const predicted = ordered.map((item) => ({
      x: new Date(item.predicted_at).getTime(),
      y: item.predicted_soh,
    }));
    const measured = ordered
      .filter((item) => item.measured_soh !== null)
      .map((item) => ({
        x: new Date(item.predicted_at).getTime(),
        y: item.measured_soh as number,
      }));
    const out = [
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
      } as (typeof out)[number]);
    }
    return out;
  }, [ordered]);

  return (
    <LineChart
      title="Predicted vs measured SoH over time"
      series={series}
      unit="%"
      formatY={(v) => `${v.toFixed(1)}`}
      formatX={formatAxisTime}
      xLabel={`Time (${localZoneName()})`}
      yMin={0}
      yMax={100}
      emptyLabel="No SoH history"
    />
  );
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
