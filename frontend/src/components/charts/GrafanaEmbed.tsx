/**
 * Grafana panel embed (PRD §4.2).
 *
 * The rolling 24-hour telemetry graphs are Grafana's job, so this renders the
 * configured dashboard in a kiosk iframe. The URL is built from public config
 * only (base URL + dashboard UID); no admin or database credential ever reaches
 * the bundle (PRD §4.2, §8). The time range is fixed to ``now-24h``→``now`` and
 * Grafana auto-refreshes, so new telemetry enters from the right and older
 * readings leave on the left without a page reload.
 *
 * The iframe surfaces its own load/error state: a Grafana that is down (or a
 * refused embedding) shows an honest message with a direct link, never a blank
 * panel that looks like "no telemetry".
 */
import { useEffect, useMemo, useState } from "react";

import { config } from "@/config";

export interface GrafanaEmbedProps {
  /** Dashboard UID; defaults to the configured Live dashboard. */
  dashboardUid?: string;
  /** Rolling window start, Grafana relative syntax (default ``now-24h``). */
  from?: string;
  /** Window end (default ``now``). */
  to?: string;
  /** Auto-refresh interval (default matches the 1 Hz ingestion). */
  refresh?: string;
  /** Template variables, e.g. ``{ "var-device": "BMS_001" }``. */
  variables?: Record<string, string>;
  /** Height of the embedded panel area. */
  height?: number;
  title?: string;
}

function buildEmbedUrl(
  dashboardUid: string,
  from: string,
  to: string,
  refresh: string,
  variables: Record<string, string>,
): string {
  const base = config.grafanaUrl.replace(/\/+$/, "");
  const url = new URL(`${base}/d/${dashboardUid}`);
  // kiosk strips Grafana's own chrome so only the panels show (PRD §4.2).
  url.searchParams.set("kiosk", "");
  url.searchParams.set("theme", "dark");
  url.searchParams.set("from", from);
  url.searchParams.set("to", to);
  url.searchParams.set("refresh", refresh);
  for (const [key, value] of Object.entries(variables)) {
    if (value) url.searchParams.set(key, value);
  }
  return url.toString();
}

export function GrafanaEmbed({
  dashboardUid = config.grafanaDashboardUid,
  from = "now-24h",
  to = "now",
  refresh = "5s",
  variables = {},
  height = 480,
  title = "Rolling 24-hour telemetry",
}: GrafanaEmbedProps) {
  const [state, setState] = useState<"loading" | "loaded" | "timeout" | "error">("loading");

  const src = useMemo(
    () => buildEmbedUrl(dashboardUid, from, to, refresh, variables),
    [dashboardUid, from, to, refresh, variables],
  );

  useEffect(() => {
    setState("loading");
    const timer = window.setTimeout(() => setState(current => current === "loading" ? "timeout" : current), 20_000);
    return () => window.clearTimeout(timer);
  }, [src]);

  return (
    <div className="grafana-embed">
      <div className="grafana-embed__bar">
        <span className="grafana-embed__range muted">
          {from} → {to} · auto-refresh {refresh}
        </span>
        <a
          className="grafana-embed__link"
          href={src}
          target="_blank"
          rel="noreferrer"
        >
          Open in Grafana →
        </a>
      </div>

      <p className="muted detail-note">Grafana requires its own viewer sign-in. A loaded frame does not prove the dashboard is authenticated or receiving data. If it is blank or shows a sign-in page, open Grafana above, sign in, then reload this page. Never enable anonymous access to bypass login.</p>
      {state === "timeout" && <p role="alert">The frame has not finished loading after 20 seconds. Check Grafana connectivity, viewer sign-in and embedding policy using the direct link above.</p>}
      {state === "error" ? (
        <div className="grafana-embed__fallback" role="alert">
          <strong>Grafana panel unavailable</strong>
          <p>
            The dashboard at <span className="mono">{config.grafanaUrl}</span>{" "}
            could not be embedded. It may be offline, or embedding may not be
            permitted for this origin.
          </p>
          <a className="btn" href={src} target="_blank" rel="noreferrer">
            Open dashboard in a new tab
          </a>
        </div>
      ) : (
        <div className="grafana-embed__frame" style={{ height }}>
          {state === "loading" && (
            <div className="grafana-embed__loading" role="status">
              Loading Grafana dashboard…
            </div>
          )}
          <iframe
            title={title}
            src={src}
            className="grafana-embed__iframe"
            onLoad={() => setState("loaded")}
            onError={() => setState("error")}
          />
        </div>
      )}
    </div>
  );
}
