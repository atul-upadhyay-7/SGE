/**
 * SoH prediction and model endpoints (FR-07, CR §7).
 *
 * Predictions come from cycle-level aggregation, never from raw readings, and
 * every row carries its model version, feature-schema version and input-quality
 * status so a score is reproducible (PRD §4.6).
 */
import { apiFetch } from "@/services/api";
import type { ModelInfo, RulInfo, SohPrediction } from "@/types/domain";

export interface PredictionHistoryQuery {
  from?: string;
  to?: string;
  order?: "asc" | "desc";
  limit?: number;
  cursor?: string;
}

/** Cursor-paginated page (backend ``SohHistoryPage``; ``total`` is not sent). */
export interface PredictionHistoryPage {
  device_id: string;
  order: string;
  limit: number;
  count: number;
  next_cursor: string | null;
  items: SohPrediction[];
}

export function getLatestPrediction(
  deviceId: string,
  signal?: AbortSignal,
): Promise<SohPrediction> {
  return apiFetch<SohPrediction>(
    `/predictions/latest/${encodeURIComponent(deviceId)}`,
    { signal },
  );
}

export function getPredictionHistory(
  deviceId: string,
  query: PredictionHistoryQuery = {},
  signal?: AbortSignal,
): Promise<PredictionHistoryPage> {
  return apiFetch<PredictionHistoryPage>(
    `/predictions/history/${encodeURIComponent(deviceId)}`,
    { query: { ...query }, signal },
  );
}

export function getActiveModels(signal?: AbortSignal): Promise<{
  count: number;
  items: ModelInfo[];
}> {
  return apiFetch<{ count: number; items: ModelInfo[] }>("/models/active", {
    signal,
  });
}

export function getRul(deviceId: string, signal?: AbortSignal): Promise<RulInfo> {
  return apiFetch<RulInfo>("/rul", { query: { device_id: deviceId }, signal });
}
