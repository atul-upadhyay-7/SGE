/**
 * Runtime configuration read once at startup.
 *
 * These are public frontend values (the backend URL and the Grafana location),
 * never secrets. Defaults keep a fresh clone usable against a local backend
 * without an .env file; see .env.example for overrides.
 */
export interface AppConfig {
  apiBaseUrl: string;
  grafanaUrl: string;
  grafanaDashboardUid: string;
}

const DEFAULT_API_BASE_URL = "http://localhost:8000/api/v1";
const DEFAULT_GRAFANA_URL = "http://localhost:3050";
const DEFAULT_GRAFANA_DASHBOARD_UID = "bms-live";

function trimmedOr(value: string | undefined, fallback: string): string {
  const trimmed = value?.trim();
  return trimmed ? trimmed.replace(/\/+$/, "") : fallback;
}

export const config: AppConfig = {
  apiBaseUrl: trimmedOr(import.meta.env.VITE_API_BASE_URL, DEFAULT_API_BASE_URL),
  grafanaUrl: trimmedOr(import.meta.env.VITE_GRAFANA_URL, DEFAULT_GRAFANA_URL),
  grafanaDashboardUid:
    import.meta.env.VITE_GRAFANA_DASHBOARD_UID?.trim() ||
    DEFAULT_GRAFANA_DASHBOARD_UID,
};
