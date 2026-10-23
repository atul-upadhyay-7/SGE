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
import { AUTH_REQUIRED_EVENT, readAccessHeaders } from "@/services/operatorKey";
import { isReportDetail, isReportPage } from "@/services/validate";
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
  return apiFetch<ReportDetail>("/reports", {
    method: "POST",
    body: payload,
    validate: isReportDetail,
  });
}

export function listReports(
  query: ReportQuery = {},
  signal?: AbortSignal,
): Promise<ReportPage> {
  return apiFetch<ReportPage>("/reports", {
    query: { ...query },
    signal,
    validate: isReportPage,
  });
}

export function getReport(
  reportId: number,
  signal?: AbortSignal,
): Promise<ReportDetail> {
  return apiFetch<ReportDetail>(`/reports/${reportId}`, {
    signal,
    validate: isReportDetail,
  });
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

  // Same guarantees as apiFetch, which this endpoint cannot use
  // because it returns a blob: a timeout (a stalled download used
  // to hang forever) and the auth-required prompt on a 401.
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 60_000);
  let response: Response;
  try {
    response = await fetch(url, {
      headers: { Accept: "*/*", ...readAccessHeaders() },
      signal: controller.signal,
    });
  } catch {
    throw new NetworkError(`Could not reach the backend at ${config.apiBaseUrl}`);
  } finally {
    window.clearTimeout(timeout);
  }

  if (response.status === 401) {
    window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
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
  // RFC 5987 filename* wins when present; it is percent-encoded.
  const star = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  let name: string | null = null;
  if (star) {
    try {
      name = decodeURIComponent(star[1]);
    } catch {
      name = null;
    }
  }
  if (!name) {
    const match = disposition.match(/filename="?([^\"]+)"?/);
    name = match ? match[1] : null;
  }
  if (!name) return `report-${reportId}.${format}`;
  // The name is server-controlled: strip any path and characters a
  // filesystem or browser would treat specially.
  const basename = name.split(/[\\/]/).pop() ?? "";
  const sanitised = basename.replace(/[?%*:|"<> \u0000-\u001f]/g, "_");
  return sanitised || `report-${reportId}.${format}`;
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
  // Revoking synchronously can cancel the download in some
  // browsers before it starts; defer it past the click hand-off.
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** List query helper kept here so the page imports a single surface. */
export type { ReportSummary };
