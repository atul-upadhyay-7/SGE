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
