/**
 * Live event stream (SSE) client.
 *
 * The backend publishes discrete events (``telemetry``, ``cycle``, ``alert``,
 * ``monitor``, ``device``) over ``GET /stream``. The client reconnects and surfaces its state so the UI can show a truthful
 * connection indicator instead of pretending to be live (PRD §4.2, §6).
 */
import { config } from "@/config";
import { apiFetch } from "@/services/api";
import { AUTH_REQUIRED_EVENT, readAccessHeaders } from "@/services/operatorKey";
import type { LiveEvent } from "@/types/domain";

export type StreamState = "connecting" | "open" | "closed" | "error";

export interface StreamHandlers {
  onEvent: (event: LiveEvent) => void;
  onState?: (state: StreamState) => void;
  kinds?: string[];
}

export interface StreamConnection {
  close: () => void;
}

const RETRY_MS = 3000;
const RETRY_MAX_MS = 60_000;
// A peer that never sends the blank-line frame boundary would otherwise
// grow this buffer without bound; a legitimate frame is a small JSON
// object, so anything past 1 MiB is a stuck/corrupt stream — drop it.
const MAX_BUFFER_CHARS = 1_048_576;

/**
 * Opens the SSE stream with ``fetch`` instead of ``EventSource`` so the viewer
 * key can travel in a header (``EventSource`` cannot set headers, and a key in
 * the URL would leak into logs). Reconnects until closed; a 401 stops it and
 * asks the app to show the sign-in prompt.
 */
export function openEventStream(handlers: StreamHandlers): StreamConnection {
  const base = config.apiBaseUrl.replace(/\/+$/, "");
  const url = new URL(`${base}/stream`);
  if (handlers.kinds?.length) {
    url.searchParams.set("kinds", handlers.kinds.join(","));
  }

  let closed = false;
  let controller: AbortController | null = null;
  let retryTimer: number | undefined;
  let retryDelay = RETRY_MS;
  handlers.onState?.("connecting");

  function dispatch(frame: string) {
    const data = frame
      .split("\n")
      .filter(line => line.startsWith("data:"))
      .map(line => line.slice(5).replace(/^ /, ""))
      .join("\n");
    if (!data) return;
    try {
      handlers.onEvent(JSON.parse(data) as LiveEvent);
    } catch {
      // A malformed frame must not tear down the stream; skip it.
    }
  }

  async function run() {
    controller = new AbortController();
    try {
      const response = await fetch(url.toString(), {
        headers: { Accept: "text/event-stream", ...readAccessHeaders() },
        signal: controller.signal,
      });
      if (response.status === 401) {
        window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
        handlers.onState?.("error");
        return;
      }
      if (!response.ok || !response.body) throw new Error(`stream ${response.status}`);
      retryDelay = RETRY_MS;
      handlers.onState?.("open");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
        if (buffer.length > MAX_BUFFER_CHARS) buffer = "";
        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1) {
          dispatch(buffer.slice(0, boundary));
          buffer = buffer.slice(boundary + 2);
          boundary = buffer.indexOf("\n\n");
        }
      }
    } catch {
      // Network failure or abort: fall through to the retry below.
    }
    if (closed) return;
    handlers.onState?.("error");
    // Exponential backoff (3s -> 60s cap): a fixed 3s retry loop made
    // every open tab hammer a permanently failing endpoint forever.
    retryTimer = window.setTimeout(run, retryDelay);
    retryDelay = Math.min(retryDelay * 2, RETRY_MAX_MS);
  }

  void run();

  return {
    close: () => {
      closed = true;
      window.clearTimeout(retryTimer);
      controller?.abort();
      handlers.onState?.("closed");
    },
  };
}

export interface StreamStatus {
  status: string;
  stream: string;
  subscribers?: number;
  published?: number;
}

/** Server-side stream health: subscriber count and published-event count. */
export function getStreamStatus(signal?: AbortSignal): Promise<StreamStatus> {
  return apiFetch<StreamStatus>("/stream/status", { signal });
}
