/**
 * Async data hook with loading/error/retry and abort-on-unmount.
 *
 * Pages use this instead of ad-hoc ``useEffect`` + ``useState`` so every page
 * gets the same four states (loading, error, empty, success) and a consistent
 * retry path (PRD §3, §8: every page handles loading/error/empty/stale).
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { isApiError, isNetworkError } from "@/services/api";

export interface AsyncState<T> {
  data: T | null;
  error: string | null;
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
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);

    fnRef
      .current(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setData(result);
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setError(describeError(caught));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);
  return { data, error, loading, reload };
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
