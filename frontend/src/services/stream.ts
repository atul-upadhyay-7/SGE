/**
 * Live event stream (SSE) client.
 *
 * The backend publishes discrete events (``telemetry``, ``cycle``, ``alert``,
 * ``monitor``, ``device``) over ``GET /stream``. ``EventSource`` handles
 * reconnection, but we surface its state so the UI can show a truthful
 * connection indicator instead of pretending to be live (PRD §4.2, §6).
 */
import { config } from "@/config";
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

export function openEventStream(handlers: StreamHandlers): StreamConnection {
  const base = config.apiBaseUrl.replace(/\/+$/, "");
  const url = new URL(`${base}/stream`);
  if (handlers.kinds?.length) {
    url.searchParams.set("kinds", handlers.kinds.join(","));
  }

  const source = new EventSource(url.toString());
  handlers.onState?.("connecting");

  source.onopen = () => handlers.onState?.("open");
  source.onerror = () => handlers.onState?.("error");
  source.onmessage = (message) => {
    try {
      const parsed = JSON.parse(message.data) as LiveEvent;
      handlers.onEvent(parsed);
    } catch {
      // A malformed frame must not tear down the stream; skip it.
    }
  };

  return {
    close: () => {
      source.close();
      handlers.onState?.("closed");
    },
  };
}
