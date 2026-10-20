/** SoH prediction and model endpoints. */
import { apiFetch } from "@/services/api";
import type { ModelInfo, RulInfo, SohPrediction } from "@/types/domain";

export interface PredictionHistoryQuery {
  from?: string;
  to?: string;
  order?: "asc" | "desc";
  limit?: number;
  cursor?: string;
}

export interface PredictionHistoryPage {
  items: SohPrediction[];
  total: number;
  next_cursor: string | null;
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
  models: ModelInfo[];
}> {
  return apiFetch<{ models: ModelInfo[] }>("/models/active", { signal });
}

export function getRul(deviceId: string, signal?: AbortSignal): Promise<RulInfo> {
  return apiFetch<RulInfo>("/rul", { query: { device_id: deviceId }, signal });
}
