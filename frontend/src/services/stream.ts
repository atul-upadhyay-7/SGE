/**
 * Live event stream (SSE) client.
 *
 * The backend publishes discrete events (``telemetry``, ``cycle``, ``alert``,
 * ``monitor``, ``device``) over ``GET /stream``. The client reconnects and surfaces its state so the UI can show a truthful
 * connection indicator instead of pretending to be live (PRD §4.2, §6).
 */
import { config } from "@/config";
import { apiFetch } from "@/services/api";
import {
  AUTH_REQUIRED_EVENT,
  AUTH_RESOLVED_EVENT,
  readAccessHeaders,
} from "@/services/operatorKey";
import { isLiveEvent } from "@/services/validate";
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
 * the URL would leak into logs). Reconnects until closed; a 401 pauses it and
 * asks the app to show the sign-in prompt, and it reconnects as soon as the
 * prompt resolves (AUTH_RESOLVED_EVENT) — the provider mounts before any key
 * is typed, so without that retry the stream would stay dead all session.
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
  let stoppedForAuth = false;
  handlers.onState?.("connecting");

  function onAuthResolved() {
    if (closed || !stoppedForAuth) return;
    stoppedForAuth = false;
    retryDelay = RETRY_MS;
    handlers.onState?.("connecting");
    void run();
  }
  window.addEventListener(AUTH_RESOLVED_EVENT, onAuthResolved);

  function dispatch(frame: string) {
    const data = frame
      .split("\n")
      .filter(line => line.startsWith("data:"))
      .map(line => line.slice(5).replace(/^ /, ""))
      .join("\n");
    if (!data) return;
    try {
      const parsed: unknown = JSON.parse(data);
      // Shape-check before handing to the UI: a parsed-but-wrong
      // frame (error JSON, a future event kind with no data) must
      // be skipped, not cast into a LiveEvent the panels trust.
      if (isLiveEvent(parsed)) handlers.onEvent(parsed);
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
        // Pause (do not retry blindly): reconnect when the sign-in
        // prompt resolves, via onAuthResolved above.
        stoppedForAuth = true;
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
      window.removeEventListener(AUTH_RESOLVED_EVENT, onAuthResolved);
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
