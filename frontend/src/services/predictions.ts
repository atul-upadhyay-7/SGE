/**
 * SoH prediction and model endpoints (FR-07, CR §7).
 *
 * Predictions come from cycle-level aggregation, never from raw readings, and
 * every row carries its model version, feature-schema version and input-quality
 * status so a score is reproducible (PRD §4.6).
 */
import { apiFetch } from "@/services/api";
import { isSohPrediction } from "@/services/validate";
import type { ModelInfo, SohPrediction } from "@/types/domain";

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
    { signal, validate: isSohPrediction },
  );
}

export function getPredictionHistory(
  deviceId: string,
  query: PredictionHistoryQuery = {},
  signal?: AbortSignal,
): Promise<PredictionHistoryPage> {
  return apiFetch<PredictionHistoryPage>(
    `/predictions/history/${encodeURIComponent(deviceId)}`,
    {
      query: { ...query },
      signal,
      validate: (parsed) =>
        typeof parsed === "object" &&
        parsed !== null &&
        Array.isArray((parsed as PredictionHistoryPage).items) &&
        (parsed as PredictionHistoryPage).items.every(isSohPrediction),
    },
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
