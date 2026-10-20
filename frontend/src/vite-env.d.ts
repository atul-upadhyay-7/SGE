/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_GRAFANA_URL?: string;
  readonly VITE_GRAFANA_DASHBOARD_UID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
