/**
 * Polling hook that never overlaps requests and records the last success.
 *
 * The 1 Hz hardware rate must not drive UI polling (PRD §6), so callers choose
 * a much slower interval. A request in flight blocks the next tick, and the
 * "last successful refresh" timestamp is exposed for the freshness indicator.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { describeError } from "@/hooks/useAsync";

export interface PollingState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  lastUpdated: Date | null;
  refresh: () => void;
}

export function usePolling<T>(
  fn: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
  deps: readonly unknown[] = [],
): PollingState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const fnRef = useRef(fn);
  fnRef.current = fn;
  const inFlight = useRef(false);
  const controllerRef = useRef<AbortController | null>(null);

  const run = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      const result = await fnRef.current(controller.signal);
      if (controller.signal.aborted) return;
      setData(result);
      setError(null);
      setLastUpdated(new Date());
    } catch (caught) {
      if (controller.signal.aborted) return;
      setError(describeError(caught));
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void run();
    const id = window.setInterval(() => void run(), intervalMs);
    return () => {
      window.clearInterval(id);
      controllerRef.current?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, intervalMs, run]);

  return { data, error, loading, lastUpdated, refresh: run };
}
