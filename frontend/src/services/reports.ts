/**
 * Historical report endpoints (CR §8, PRD §4.7).
 *
 * A report is generated once and stored; the read endpoints return that stored
 * snapshot unchanged, so it stays reproducible after the underlying telemetry
 * ages out. Downloads return a raw body (JSON or CSV), not the app's JSON
 * envelope, so they are fetched as blobs and handed to the browser directly.
 */
import { config } from "@/config";
import { ApiError, apiFetch, NetworkError } from "@/services/api";
import type {
  ReportCreate,
  ReportDetail,
  ReportPage,
  ReportSummary,
} from "@/types/domain";

export interface ReportQuery {
  device_id?: string;
  report_type?: string;
  offset?: number;
  limit?: number;
}

export function createReport(payload: ReportCreate): Promise<ReportDetail> {
  return apiFetch<ReportDetail>("/reports", { method: "POST", body: payload });
}

export function listReports(
  query: ReportQuery = {},
  signal?: AbortSignal,
): Promise<ReportPage> {
  return apiFetch<ReportPage>("/reports", { query: { ...query }, signal });
}

export function getReport(
  reportId: number,
  signal?: AbortSignal,
): Promise<ReportDetail> {
  return apiFetch<ReportDetail>(`/reports/${reportId}`, { signal });
}

export interface DownloadedReport {
  blob: Blob;
  filename: string;
}

/**
 * Download a stored report as JSON or CSV.
 *
 * The endpoint sets ``Content-Disposition``; we read the filename from it and
 * fall back to a predictable name. A non-2xx status is normalised to
 * :class:`ApiError` so callers can surface the backend's message.
 */
export async function downloadReport(
  reportId: number,
  format: "json" | "csv" | "pdf",
): Promise<DownloadedReport> {
  const base = config.apiBaseUrl.replace(/\/+$/, "");
  const url = `${base}/reports/${reportId}/download?format=${format}`;

  let response: Response;
  try {
    response = await fetch(url, { headers: { Accept: "*/*" } });
  } catch {
    throw new NetworkError(`Could not reach the backend at ${config.apiBaseUrl}`);
  }

  if (!response.ok) {
    const text = await response.text();
    let detail: unknown = text;
    try {
      detail = JSON.parse(text);
    } catch {
      /* keep raw text */
    }
    throw new ApiError(
      `Download failed with status ${response.status}`,
      response.status,
      detail,
    );
  }

  const blob = await response.blob();
  return { blob, filename: filenameFrom(response, reportId, format) };
}

function filenameFrom(
  response: Response,
  reportId: number,
  format: string,
): string {
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = disposition.match(/filename="?([^\"]+)"?/);
  return match ? match[1] : `report-${reportId}.${format}`;
}

/** Trigger a browser download for an already-fetched blob. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

/** List query helper kept here so the page imports a single surface. */
export type { ReportSummary };
