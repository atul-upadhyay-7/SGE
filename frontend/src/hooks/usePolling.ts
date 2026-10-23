/** Non-overlapping polling, isolated per device/filter generation. */
import { useEffect, useRef, useState } from "react";
import { describeError, errorStatusOf } from "@/hooks/useAsync";
import { AUTH_RESOLVED_EVENT } from "@/services/operatorKey";

export interface PollingState<T> {
  data: T | null;
  error: string | null;
  /** HTTP status when the error is an ApiError, else null. */
  errorStatus: number | null;
  loading: boolean;
  lastUpdated: Date | null;
  refresh: () => void;
}

export function usePolling<T>(
  fn: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
  deps: readonly unknown[] = [],
  enabled = true,
): PollingState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorStatus, setErrorStatus] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const refreshRef = useRef<() => void>(() => {});

  // Reset only when the request inputs change; an interval-only change
  // (e.g. faster polling while a report is queued) must not blank the data,
  // or the view would flap between Loading and the list on every tick.
  useEffect(() => {
    setData(null);
    setError(null);
    setErrorStatus(null);
    setLastUpdated(null);
    setLoading(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps]);

  useEffect(() => {
    if (!enabled) {
      // Disabled polling (e.g. a stored report that has reached its
      // terminal state) must not fetch or tick at all.
      setLoading(false);
      refreshRef.current = () => {};
      return;
    }
    let active = true;
    let inFlight = false;
    let controller: AbortController | null = null;
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
        setErrorStatus(null);
        setLastUpdated(new Date());
      } catch (caught) {
        if (!active || request.signal.aborted) return;
        setError(describeError(caught));
        setErrorStatus(errorStatusOf(caught));
      } finally {
        inFlight = false;
        if (active) setLoading(false);
      }
    };
    refreshRef.current = () => void run();
    void run();
    // A hidden tab has no reader: polling it burns the backend and
    // the battery for updates nobody sees. Skip ticks while hidden
    // and catch up immediately when the tab becomes visible again.
    const tick = () => {
      if (document.visibilityState === "hidden") return;
      void run();
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") void run();
    };
    const id = window.setInterval(tick, intervalMs);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      active = false;
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisibility);
      controller?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, intervalMs, enabled]);

  // Reload promptly when a key prompt completes (see useAsync).
  useEffect(() => {
    const onResolved = () => refreshRef.current();
    window.addEventListener(AUTH_RESOLVED_EVENT, onResolved);
    return () => window.removeEventListener(AUTH_RESOLVED_EVENT, onResolved);
  }, []);

  return { data, error, errorStatus, loading, lastUpdated, refresh: () => refreshRef.current() };
}
