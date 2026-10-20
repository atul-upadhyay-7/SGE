/**
 * Shared domain types.
 *
 * Shapes are taken from the backend response schemas (``app/schemas``) so the
 * UI and API client agree. Anomaly and report types follow the frontend PRD's
 * contract; the backend endpoints for those land in a later milestone, and the
 * client is written so those pages light up without a type change.
 */

export type Severity = "info" | "warning" | "critical";

export type DeviceStatus = "active" | "inactive" | "retired";

export interface Device {
  id: number;
  device_id: string;
  name: string;
  cell_count: number;
  sampling_frequency_hz: number;
  chemistry: string;
  nominal_capacity_ah: number | null;
  status: DeviceStatus | string;
  created_at: string | null;
  last_seen_at: string | null;
}

export interface TelemetrySample {
  device_id: string;
  message_id: string;
  measured_at: string;
  received_at: string;
  pack_voltage: number | null;
  current: number;
  temperature: number;
  cell_voltages: number[];
  additional_data: Record<string, unknown> | null;
  quality_flags: Record<string, unknown> | null;
}

export interface TelemetryPage {
  items: TelemetrySample[];
  total: number;
  next_cursor: string | null;
}

export interface TelemetrySummary {
  device_id: string;
  sample_count: number;
  first_measured_at: string | null;
  last_measured_at: string | null;
  min_pack_voltage: number | null;
  max_pack_voltage: number | null;
  avg_pack_voltage: number | null;
  min_current: number | null;
  max_current: number | null;
  avg_current: number | null;
  min_temperature: number | null;
  max_temperature: number | null;
  avg_temperature: number | null;
}

export interface SohPrediction {
  device_id: string;
  cycle_id: number | null;
  predicted_soh: number;
  measured_soh: number | null;
  model_version: string;
  feature_schema_version: string;
  predicted_at: string;
  input_quality_status: string;
}

export interface ModelInfo {
  model_name: string;
  model_version: string;
  feature_schema_version: string;
  training_dataset_version: string | null;
  metrics: Record<string, unknown> | null;
  created_at: string | null;
}

export interface RulInfo {
  device_id: string;
  status: string;
  rul_discharges: number | null;
  p10_discharges: number | null;
  p90_discharges: number | null;
  model_version: string | null;
  message: string | null;
}

export interface Alert {
  id: number;
  device_id: string;
  rule_code: string;
  severity: Severity | string;
  message: string;
  details: Record<string, unknown> | null;
  status: string;
  first_seen_at: string;
  last_seen_at: string;
  resolved_at: string | null;
  acked_at: string | null;
}

/** Backend health is reported per subsystem and must stay distinguishable. */
export interface HealthStatus {
  status: string;
  [key: string]: unknown;
}

export interface LiveEvent {
  kind: string;
  at: string;
  data: Record<string, unknown>;
}
