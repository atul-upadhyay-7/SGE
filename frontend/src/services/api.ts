/**
 * The single HTTP boundary for the app.
 *
 * Every request goes through :func:`apiFetch`, so base URL, timeouts, JSON
 * handling and error shape live in exactly one place. Errors are normalised to
 * :class:`ApiError` so pages can render a consistent message without guessing
 * at fetch/HTTP/parse failures.
 */
import { getOperatorKey } from "@/services/operatorKey";
import { config } from "@/config";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export class NetworkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NetworkError";
  }
}

export interface RequestOptions {
  method?: string;
  query?: Record<string, string | number | boolean | undefined | null>;
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  timeoutMs?: number;
}

const DEFAULT_TIMEOUT_MS = 10_000;
const OPERATOR_KEY_HEADER = "X-Operator-Key";

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const base = config.apiBaseUrl.replace(/\/+$/, "");
  const suffix = path.startsWith("/") ? path : `/${path}`;
  const url = new URL(`${base}${suffix}`);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null || value === "") continue;
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

function messageFor(status: number, detail: unknown): string {
  if (status === 401) {
    return "This action needs the operator key. Enter it with the Operator key control in the top bar, then try again.";
  }
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object" && "detail" in detail) {
    const inner = (detail as { detail: unknown }).detail;
    if (typeof inner === "string") return inner;
  }
  return `Request failed with status ${status}`;
}

export async function apiFetch<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { method = "GET", query, body, headers, signal, timeoutMs } = options;
  const controller = new AbortController();
  const timeout = window.setTimeout(
    () => controller.abort(),
    timeoutMs ?? DEFAULT_TIMEOUT_MS,
  );

  // Forward an external abort signal to our controller so callers can cancel.
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", () => controller.abort(), { once: true });
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      headers: {
        Accept: "application/json",
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(method !== "GET" && getOperatorKey()
          ? { [OPERATOR_KEY_HEADER]: getOperatorKey() as string }
          : {}),
        ...headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new NetworkError("The request was cancelled or timed out");
    }
    throw new NetworkError(
      `Could not reach the backend at ${config.apiBaseUrl}`,
    );
  } finally {
    window.clearTimeout(timeout);
  }

  const text = await response.text();
  const parsed: unknown = text ? safeParse(text) : null;

  if (!response.ok) {
    throw new ApiError(messageFor(response.status, parsed), response.status, parsed);
  }
  return parsed as T;
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** True when the value is a network/timeout failure rather than an HTTP error. */
export function isNetworkError(error: unknown): error is NetworkError {
  return error instanceof NetworkError;
}

/** True when the backend answered with a non-2xx status. */
export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}
