/**
 * Operator key for dashboard write actions.
 *
 * Kept in sessionStorage only: it is typed in by the operator, never baked
 * into the production bundle, and is gone when the tab closes.
 */
const STORAGE_KEY = "bms.operatorKey";

export function getOperatorKey(): string | null {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY) || null;
  } catch {
    return null;
  }
}

export function setOperatorKey(value: string): void {
  try {
    if (value.trim()) window.sessionStorage.setItem(STORAGE_KEY, value.trim());
    else window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage unavailable: writes will simply be refused by the backend */
  }
}

/** Viewer key for read access, same storage rules as the operator key. */
const VIEWER_STORAGE_KEY = "bms.viewerKey";

export function getViewerKey(): string | null {
  try {
    return window.sessionStorage.getItem(VIEWER_STORAGE_KEY) || null;
  } catch {
    return null;
  }
}

export function setViewerKey(value: string): void {
  try {
    if (value.trim()) window.sessionStorage.setItem(VIEWER_STORAGE_KEY, value.trim());
    else window.sessionStorage.removeItem(VIEWER_STORAGE_KEY);
  } catch {
    /* storage unavailable */
  }
}

/** Header carrying the stored keys for read requests. */
export function readAccessHeaders(): Record<string, string> {
  const viewer = getViewerKey();
  return viewer ? { "X-Viewer-Key": viewer } : {};
}

export const AUTH_REQUIRED_EVENT = "bms:auth-required";
