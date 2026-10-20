/** Telemetry read endpoints (latest / history / summary). */
import { apiFetch } from "@/services/api";
import type { TelemetryPage, TelemetrySample, TelemetrySummary } from "@/types/domain";

export interface HistoryQuery {
  from?: string;
  to?: string;
  order?: "asc" | "desc";
  limit?: number;
  cursor?: string;
}

export function getLatestTelemetry(
  deviceId: string,
  signal?: AbortSignal,
): Promise<TelemetrySample> {
  return apiFetch<TelemetrySample>(
    `/telemetry/latest/${encodeURIComponent(deviceId)}`,
    { signal },
  );
}

export function getTelemetryHistory(
  deviceId: string,
  query: HistoryQuery = {},
  signal?: AbortSignal,
): Promise<TelemetryPage> {
  return apiFetch<TelemetryPage>(
    `/telemetry/history/${encodeURIComponent(deviceId)}`,
    { query: { ...query }, signal },
  );
}

export function getTelemetrySummary(
  deviceId: string,
  query: Pick<HistoryQuery, "from" | "to"> = {},
  signal?: AbortSignal,
): Promise<TelemetrySummary> {
  return apiFetch<TelemetrySummary>(
    `/telemetry/summary/${encodeURIComponent(deviceId)}`,
    { query: { ...query }, signal },
  );
}
