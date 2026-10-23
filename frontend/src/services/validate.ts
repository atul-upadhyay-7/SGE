/**
 * Runtime shape checks for HTTP payloads.
 *
 * TypeScript types are erased at the boundary: ``apiFetch`` used to
 * return ``parsed as T``, so a backend change (or a proxy error page
 * with a 200) flowed into pages as a confidently-typed lie and
 * crashed renderers far from the cause. The critical payloads —
 * predictions, reports, stream events — are checked here, at the
 * boundary, and a shape mismatch becomes an ApiError the page can
 * show instead of a white screen.
 */
import type {
  LiveEvent,
  ReportDetail,
  ReportPage,
  SohPrediction,
  ValidationReport,
} from "@/types/domain";

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function isSohPrediction(value: unknown): value is SohPrediction {
  return (
    isRecord(value) &&
    isFiniteNumber(value.id) &&
    typeof value.device_id === "string" &&
    isFiniteNumber(value.predicted_soh) &&
    typeof value.model_version === "string" &&
    typeof value.predicted_at === "string"
  );
}

export function isReportSummary(value: unknown): boolean {
  return (
    isRecord(value) &&
    isFiniteNumber(value.id) &&
    typeof value.status === "string" &&
    typeof value.report_type === "string" &&
    isFiniteNumber(value.row_count)
  );
}

export function isReportPage(value: unknown): value is ReportPage {
  return (
    isRecord(value) &&
    Array.isArray(value.items) &&
    value.items.every(isReportSummary) &&
    isFiniteNumber(value.total)
  );
}

export function isReportDetail(value: unknown): value is ReportDetail {
  return (
    isReportSummary(value) &&
    isRecord(value) &&
    "payload" in value &&
    (value.payload === null || isRecord(value.payload))
  );
}

export function isValidationReport(value: unknown): value is ValidationReport {
  if (!isRecord(value)) return false;
  if (
    typeof value.generated_at !== "string" ||
    typeof value.source !== "string" ||
    typeof value.dataset !== "string" ||
    !isRecord(value.model) ||
    typeof value.model.algorithm !== "string" ||
    !isRecord(value.summary) ||
    !Array.isArray(value.rows) ||
    !Array.isArray(value.notes)
  ) {
    return false;
  }
  const summary = value.summary;
  return (
    isFiniteNumber(summary.scored_rows) &&
    isFiniteNumber(summary.refused_rows) &&
    (summary.mae_pct === null || isFiniteNumber(summary.mae_pct)) &&
    (summary.rmse_pct === null || isFiniteNumber(summary.rmse_pct)) &&
    value.rows.every(
      (row) =>
        isRecord(row) &&
        typeof row.cell_id === "string" &&
        isFiniteNumber(row.discharge_index) &&
        isFiniteNumber(row.actual_soh_pct) &&
        typeof row.status === "string",
    )
  );
}

export function isLiveEvent(value: unknown): value is LiveEvent {
  return (
    isRecord(value) &&
    typeof value.kind === "string" &&
    typeof value.at === "string" &&
    isRecord(value.data)
  );
}
