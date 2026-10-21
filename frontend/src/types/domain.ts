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
  status: DeviceStatus | string;
  last_seen_at: string | null;
  sample_count: number;
  first_measured_at: string | null;
  last_measured_at: string | null;
  rejection_count: number;
  rejections_by_reason: Record<string, number>;
}

export type AnomalyStatus =
  | "open"
  | "acknowledged"
  | "investigating"
  | "resolved";

export type AnomalyActionType =
  | "acknowledge"
  | "note"
  | "corrective_action"
  | "resolve";

/** One diagnosed anomaly episode (backend ``AnomalyEventRead``). */
export interface AnomalyEvent {
  id: number;
  device_id: string;
  anomaly_type: string;
  severity: Severity | string;
  affected_cell: number | null;
  detected_at: string;
  last_detected_at: string;
  ended_at: string | null;
  observed_value: number | null;
  expected_value: number | null;
  threshold: number | null;
  duration_seconds: number;
  status: AnomalyStatus | string;
  diagnostic_evidence: Record<string, unknown> | null;
  likely_causes: AnomalyCause[] | null;
  recommended_actions: AnomalyRecommendation[] | null;
  rule_version: string;
  created_at: string;
  updated_at: string;
}

/**
 * A possible cause from the versioned rule set. ``confirmed`` is false unless
 * validated diagnostic logic backs it (PRD §4.4), so the UI must never present
 * a cause as established when this is false.
 */
export interface AnomalyCause {
  cause: string;
  confirmed?: boolean;
  evidence?: string | null;
  [key: string]: unknown;
}

export interface AnomalyRecommendation {
  action: string;
  priority?: string | null;
  category?: string | null;
  [key: string]: unknown;
}

export interface AnomalyPage {
  device_id: string | null;
  count: number;
  limit: number;
  offset: number;
  total: number;
  items: AnomalyEvent[];
}

export interface AnomalyAction {
  id: number;
  anomaly_id: number;
  action_type: AnomalyActionType | string;
  actor: string | null;
  note: string | null;
  evidence: Record<string, unknown> | null;
  created_at: string;
}

export interface SeverityCount {
  severity: string;
  count: number;
}

export interface TypeCount {
  anomaly_type: string;
  count: number;
}

export interface AnomalySummary {
  device_id: string | null;
  from: string | null;
  to: string | null;
  total: number;
  open: number;
  acknowledged: number;
  investigating: number;
  resolved: number;
  by_severity: SeverityCount[];
  by_type: TypeCount[];
  first_detected_at: string | null;
  last_detected_at: string | null;
  avg_duration_seconds: number | null;
  unresolved: number;
}

export interface HourlyBucket {
  hour: string;
  total: number;
  open: number;
  resolved: number;
  by_severity: Record<string, number>;
}

export interface AnomalyHourly {
  device_id: string | null;
  from: string | null;
  to: string | null;
  buckets: HourlyBucket[];
}

export interface RecurrenceEntry {
  device_id: string;
  anomaly_type: string;
  affected_cell: number | null;
  occurrences: number;
  first_detected_at: string;
  last_detected_at: string;
  max_duration_seconds: number;
  unresolved: number;
}

export interface RecurrenceReport {
  from: string | null;
  to: string | null;
  entries: RecurrenceEntry[];
}

export interface SohPrediction {
  id: number;
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
  artifact_path: string;
  feature_schema_version: string;
  training_dataset_version: string | null;
  metrics: Record<string, unknown> | null;
  is_active: boolean;
  created_at: string | null;
}

/** Which optional sections a report request includes (PRD §4.7). */
export interface ReportSections {
  telemetry: boolean;
  soh: boolean;
  anomalies: boolean;
  actions: boolean;
}

export type ReportFormat = "full";

export interface ReportFilters {
  device_id: string | null;
  from: string | null;
  to: string | null;
  report_type: string;
  severity: string | null;
  anomaly_type: string | null;
  sections: ReportSections;
}

/** A lightweight listing row (backend ``ReportSummary``). */
export interface ReportSummary {
  id: number;
  report_type: string;
  device_id: string | null;
  period_start: string | null;
  period_end: string | null;
  status: string;
  format_version: string;
  table_name: string | null;
  row_count: number;
  generated_by: string | null;
  created_at: string;
}

export interface ReportPage {
  count: number;
  limit: number;
  offset: number;
  total: number;
  items: ReportSummary[];
}

/**
 * The rendered snapshot. Its shape follows the enabled filters, so it is
 * modelled loosely: the blocks the UI reads are typed, and anything else stays
 * accessible rather than dropped.
 */
export interface ReportPayload {
  format_version: string;
  report_type: string;
  generated_at: string;
  period: { from: string | null; to: string | null };
  filters: ReportFilters;
  included_sections: string[];
  device: {
    device_id: string | null;
    name: string | null;
    scope: string;
    cell_count?: number;
    chemistry?: string;
    sampling_frequency_hz?: number;
    status?: string;
  };
  data_availability: {
    scope: string;
    sample_count?: number;
    first_measured_at?: string | null;
    last_measured_at?: string | null;
    rejection_count?: number;
    rejections_by_reason?: Record<string, number>;
  };
  telemetry?: {
    sample_count: number;
    pack_voltage: Record<string, number | null>;
    current: Record<string, number | null>;
    temperature: Record<string, number | null>;
  };
  soh?: {
    count: number;
    latest_predicted_soh: number | null;
    history: {
      predicted_at: string;
      predicted_soh: number;
      measured_soh: number | null;
      cycle_id: number | null;
      model_version: string;
      feature_schema_version: string;
      input_quality_status: string;
    }[];
  };
  anomalies?: {
    total: number;
    status_counts: Record<string, number>;
    by_severity: CountMap;
    by_type: CountMap;
    hourly: {
      hour: string;
      total: number;
      open: number;
      resolved: number;
      by_severity: Record<string, number>;
    }[];
    list_count: number;
    events: ReportEventRow[];
    unresolved_count: number;
    unresolved: ReportEventRow[];
  };
  actions?: {
    count: number;
    items: {
      anomaly_id: number;
      device_id: string;
      anomaly_type: string;
      action_type: string;
      actor: string | null;
      note: string | null;
      created_at: string;
    }[];
  };
  model_metadata: {
    active_models: {
      model_name: string;
      model_version: string;
      feature_schema_version: string;
    }[];
    rules_version: string;
  };
  summary: {
    device: string | null;
    period: { from: string | null; to: string | null };
    samples: number | null;
    anomalies_total: number;
    anomalies_unresolved: number;
    latest_predicted_soh: number | null;
  };
  [key: string]: unknown;
}

export interface ReportEventRow {
  id: number;
  device_id: string;
  detected_at: string;
  last_detected_at: string;
  anomaly_type: string;
  severity: string;
  affected_cell: number | null;
  status: string;
  observed_value: number | null;
  expected_value: number | null;
  threshold: number | null;
  duration_seconds: number;
  rule_version: string;
  likely_causes: unknown[] | null;
  recommended_actions: unknown[] | null;
}

/** The full stored report (backend ``ReportDetail``). */
export interface ReportDetail extends ReportSummary {
  filters: ReportFilters | null;
  sections: ReportSections | null;
  payload: ReportPayload | null;
}

export interface ReportCreate {
  device_id?: string | null;
  from?: string | null;
  to?: string | null;
  report_type?: ReportFormat;
  severity?: string | null;
  anomaly_type?: string | null;
  sections?: ReportSections;
  generated_by?: string | null;
}

/**
 * A count map. The backend serialises these maps either as an object
 * ``{"critical": 1}`` or as an array of pairs ``[["critical", 1]]`` depending on
 * the source (a Counter vs a plain dict), so consumers normalise both.
 */
export type CountMap = Record<string, number> | [string, number][];

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
