/** Non-overlapping polling, isolated per device/filter generation. */
import { useEffect, useRef, useState } from "react";
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
  const refreshRef = useRef<() => void>(() => {});

  useEffect(() => {
    let active = true;
    let inFlight = false;
    let controller: AbortController | null = null;
    // Never keep another device's values while the new request is pending.
    setData(null);
    setError(null);
    setLastUpdated(null);
    setLoading(true);
    const run = async () => {
      if (!active || inFlight) return;
      inFlight = true;
      const request = new AbortController();
      controller = request;
      try {
        const result = await fnRef.current(request.signal);
        if (!active || request.signal.aborted) return;
        setData(result);
        setError(null);
        setLastUpdated(new Date());
      } catch (caught) {
        if (!active || request.signal.aborted) return;
        setError(describeError(caught));
      } finally {
        inFlight = false;
        if (active) setLoading(false);
      }
    };
    refreshRef.current = () => void run();
    void run();
    const id = window.setInterval(() => void run(), intervalMs);
    return () => {
      active = false;
      window.clearInterval(id);
      controller?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, intervalMs]);

  return { data, error, loading, lastUpdated, refresh: () => refreshRef.current() };
}
