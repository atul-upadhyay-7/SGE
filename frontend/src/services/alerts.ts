/** Device alert endpoints (Phase 5 rules: SoH, temperature, staleness, imbalance). */
import { apiFetch } from "@/services/api";
import type { Alert } from "@/types/domain";

export interface AlertQuery {
  device_id?: string;
  status?: string;
  severity?: string;
  limit?: number;
  offset?: number;
}

export interface AlertPage {
  count: number;
  total: number;
  items: Alert[];
}

export function listAlerts(
  query: AlertQuery = {},
  signal?: AbortSignal,
): Promise<AlertPage> {
  return apiFetch<AlertPage>("/alerts", { query: { ...query }, signal });
}

export function getDeviceAlerts(
  deviceId: string,
  query: Omit<AlertQuery, "device_id"> = {},
  signal?: AbortSignal,
): Promise<AlertPage> {
  return apiFetch<AlertPage>(
    `/devices/${encodeURIComponent(deviceId)}/alerts`,
    { query: { ...query }, signal },
  );
}

export function acknowledgeAlert(
  alertId: number,
  ackedBy: string,
): Promise<Alert> {
  return apiFetch<Alert>(`/alerts/${alertId}/ack`, {
    method: "POST",
    body: { acked_by: ackedBy },
  });
}
