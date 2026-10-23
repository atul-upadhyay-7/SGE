/**
 * Async data hook with loading/error/retry and abort-on-unmount.
 *
 * Pages use this instead of ad-hoc ``useEffect`` + ``useState`` so every page
 * gets the same four states (loading, error, empty, success) and a consistent
 * retry path (PRD §3, §8: every page handles loading/error/empty/stale).
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { isApiError, isNetworkError } from "@/services/api";
import { AUTH_RESOLVED_EVENT } from "@/services/operatorKey";

export interface AsyncState<T> {
  data: T | null;
  error: string | null;
  /** HTTP status when the error is an ApiError, else null. Lets
   * pages branch on the typed status (404 = "not there yet")
   * instead of comparing against a rendered message string. */
  errorStatus: number | null;
  loading: boolean;
  reload: () => void;
}

/**
 * Run ``fn`` on mount and whenever ``deps`` change.
 *
 * ``fn`` receives an AbortSignal; pass it to the API client so a superseded or
 * unmounted request is cancelled rather than applied to stale state.
 */
export function useAsync<T>(
  fn: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[] = [],
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorStatus, setErrorStatus] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setErrorStatus(null);
    // Never present the previous dep's data as the new dep's: on the
    // Anomaly Details route, navigating anomaly A -> B briefly rendered
    // A's data *with A's resolution actions enabled* while B loaded.
    setData(null);

    fnRef
      .current(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setData(result);
          setErrorStatus(null);
        }
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setError(describeError(caught));
        setErrorStatus(errorStatusOf(caught));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  // A completed key prompt unblocks requests app-wide; reload what
  // the 401 interrupted instead of leaving this hook's error up.
  useEffect(() => {
    const onResolved = () => reload();
    window.addEventListener(AUTH_RESOLVED_EVENT, onResolved);
    return () => window.removeEventListener(AUTH_RESOLVED_EVENT, onResolved);
  }, [reload]);

  return { data, error, errorStatus, loading, reload };
}

/** The HTTP status carried by an ApiError, or null for any other failure. */
export function errorStatusOf(error: unknown): number | null {
  return isApiError(error) ? error.status : null;
}

export function describeError(error: unknown): string {
  if (isNetworkError(error)) return error.message;
  if (isApiError(error)) {
    if (error.status === 404) return "Not found";
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}
