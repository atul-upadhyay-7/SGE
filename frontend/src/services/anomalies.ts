/**
 * Anomaly endpoints (CR §3, §4, §6).
 *
 * Reads surface what the versioned detector persisted; the acknowledge, action
 * and resolve calls append to an anomaly's audit trail. Anomalies are never
 * created from the client — the API only opens an episode from a real detection
 * — so this module exposes no create function (PRD §4.3, §4.4).
 */
import { apiFetch } from "@/services/api";
import type {
  AnomalyAction,
  AnomalyActionType,
  AnomalyEvent,
  AnomalyHourly,
  AnomalyPage,
  AnomalySummary,
  RecurrenceReport,
} from "@/types/domain";

export interface AnomalyQuery {
  device_id?: string;
  severity?: string;
  anomaly_type?: string;
  status?: string;
  from?: string;
  to?: string;
  offset?: number;
  limit?: number;
}

export interface AnomalyRangeQuery {
  device_id?: string;
  from?: string;
  to?: string;
}

export function listAnomalies(
  query: AnomalyQuery = {},
  signal?: AbortSignal,
): Promise<AnomalyPage> {
  return apiFetch<AnomalyPage>("/anomalies", { query: { ...query }, signal });
}

export function getAnomalySummary(
  query: AnomalyRangeQuery = {},
  signal?: AbortSignal,
): Promise<AnomalySummary> {
  return apiFetch<AnomalySummary>("/anomalies/summary", {
    query: { ...query },
    signal,
  });
}

export function getAnomalyHourly(
  query: AnomalyRangeQuery = {},
  signal?: AbortSignal,
): Promise<AnomalyHourly> {
  return apiFetch<AnomalyHourly>("/anomalies/hourly", {
    query: { ...query },
    signal,
  });
}

export function getAnomalyRecurrence(
  query: AnomalyRangeQuery & { limit?: number } = {},
  signal?: AbortSignal,
): Promise<RecurrenceReport> {
  return apiFetch<RecurrenceReport>("/anomalies/recurrence", {
    query: { ...query },
    signal,
  });
}

export function getAnomaly(
  anomalyId: number,
  signal?: AbortSignal,
): Promise<AnomalyEvent> {
  return apiFetch<AnomalyEvent>(`/anomalies/${anomalyId}`, { signal });
}

export function getAnomalyHistory(
  anomalyId: number,
  signal?: AbortSignal,
): Promise<AnomalyAction[]> {
  return apiFetch<AnomalyAction[]>(`/anomalies/${anomalyId}/history`, {
    signal,
  });
}

export interface AnomalyActorPayload {
  actor?: string | null;
  note?: string | null;
}

export function acknowledgeAnomaly(
  anomalyId: number,
  payload: AnomalyActorPayload = {},
): Promise<AnomalyEvent> {
  return apiFetch<AnomalyEvent>(`/anomalies/${anomalyId}/acknowledge`, {
    method: "POST",
    body: payload,
  });
}

export interface AnomalyActionPayload extends AnomalyActorPayload {
  action_type: AnomalyActionType;
  evidence?: Record<string, unknown> | null;
}

export function addAnomalyAction(
  anomalyId: number,
  payload: AnomalyActionPayload,
): Promise<AnomalyAction> {
  return apiFetch<AnomalyAction>(`/anomalies/${anomalyId}/actions`, {
    method: "POST",
    body: payload,
  });
}

export function resolveAnomaly(
  anomalyId: number,
  payload: AnomalyActorPayload = {},
): Promise<AnomalyEvent> {
  return apiFetch<AnomalyEvent>(`/anomalies/${anomalyId}/resolve`, {
    method: "POST",
    body: payload,
  });
}
