import type { Page, Route } from "@playwright/test";

export const API = "http://localhost:8000/api/v1";

export const ROUTES = [
  "/",
  "/live",
  "/anomalies",
  "/health",
  "/reports",
  "/devices",
  "/system",
] as const;

export const emptyPage = {
  items: [],
  total: 0,
  count: 0,
  limit: 50,
  offset: 0,
  next_cursor: null,
};

export function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

/** Collects uncaught page errors so a crash fails the test with its message. */
export function trackCrashes(page: Page): string[] {
  const crashes: string[] = [];
  page.on("pageerror", error => crashes.push(error.message));
  return crashes;
}

/** Backend down: every API call fails at the network layer. */
export async function backendOffline(page: Page) {
  await page.route(`${API}/**`, route => route.abort("connectionrefused"));
}

/** Backend up but with no devices or data. */
export async function backendEmpty(page: Page) {
  await page.route(`${API}/**`, route => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    if (path === "/devices") return json(route, []);
    if (path.startsWith("/health")) return json(route, { status: "ok" });
    if (path === "/models/active") return json(route, { count: 0, items: [] });
    if (path === "/stream" ) return route.abort();
    if (path === "/stream/status") return json(route, { subscribers: 0, published: 0 });
    if (path === "/anomalies/summary") {
      return json(route, {
        device_id: null, from: null, to: null, total: 0, open: 0, acknowledged: 0,
        investigating: 0, resolved: 0, by_severity: [], by_type: [],
        first_detected_at: null, last_detected_at: null,
        avg_duration_seconds: null, unresolved: 0,
      });
    }
    if (path === "/anomalies/hourly") {
      return json(route, { device_id: null, from: null, to: null, buckets: [] });
    }
    if (path === "/anomalies/recurrence") {
      return json(route, { from: null, to: null, entries: [] });
    }
    if (path.includes("/latest/") || path === "/rul") {
      return json(route, { detail: "no data" }, 404);
    }
    return json(route, emptyPage);
  });
}

export const deviceA = {
  device_id: "PACK_A",
  name: "Pack A",
  cell_count: 3,
  has_credential: false,
};
export const deviceB = { ...deviceA, device_id: "PACK_B", name: "Pack B" };
