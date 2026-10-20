/**
 * Health endpoints.
 *
 * The backend reports application, database, MQTT, ingestion and monitor
 * health *separately* (PRD §4.9: a healthy API does not imply the BMS is
 * sending data), so each is exposed as its own call.
 */
import { apiFetch } from "@/services/api";
import type { HealthStatus } from "@/types/domain";

export function getHealth(signal?: AbortSignal): Promise<HealthStatus> {
  return apiFetch<HealthStatus>("/health", { signal });
}

export function getDatabaseHealth(signal?: AbortSignal): Promise<HealthStatus> {
  return apiFetch<HealthStatus>("/health/database", { signal });
}

export function getMqttHealth(signal?: AbortSignal): Promise<HealthStatus> {
  return apiFetch<HealthStatus>("/health/mqtt", { signal });
}

export function getIngestionHealth(signal?: AbortSignal): Promise<HealthStatus> {
  return apiFetch<HealthStatus>("/health/ingestion", { signal });
}

export function getMonitorHealth(signal?: AbortSignal): Promise<HealthStatus> {
  return apiFetch<HealthStatus>("/health/monitor", { signal });
}
