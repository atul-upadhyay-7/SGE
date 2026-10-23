import { expect, test } from "@playwright/test";

import { API, backendEmpty, json } from "./helpers";

const REPORT_ID = 90;

function reportDetail(status: string) {
  return {
    id: REPORT_ID,
    report_type: "full",
    device_id: null,
    period_start: null,
    period_end: null,
    status,
    format_version: "report-v3",
    table_name: "anomaly_events",
    row_count: status === "ready" ? 3 : 0,
    generated_by: null,
    created_at: "2026-10-23T02:00:00Z",
    filters: null,
    sections: { telemetry: true, soh: true, anomalies: true, actions: true },
    payload:
      status === "ready"
        ? null // not needed for this regression; list behaviour is the point
        : { request: { queued: true } },
  };
}

function reportPage(status: string) {
  return {
    items: [reportDetail(status)],
    total: 1,
    count: 1,
    limit: 50,
    offset: 0,
  };
}

test("a queued report is submitted with queued=true and the list follows its status", async ({ page }) => {
  await backendEmpty(page);
  let status = "queued";
  let postedBody: { queued?: boolean } | null = null;

  await page.route(`${API}/reports?*`, route => {
    if (route.request().method() === "POST") {
      postedBody = route.request().postDataJSON() as { queued?: boolean };
      return json(route, reportDetail("queued"), 202);
    }
    return json(route, reportPage(status));
  });
  await page.route(`${API}/reports`, route => {
    if (route.request().method() === "POST") {
      postedBody = route.request().postDataJSON() as { queued?: boolean };
      return json(route, reportDetail("queued"), 202);
    }
    return json(route, reportPage(status));
  });
  await page.route(`${API}/reports/${REPORT_ID}`, route =>
    json(route, reportDetail(status)),
  );

  await page.goto("/reports");
  await page.getByLabel(/Queue in background/).check();
  await page.getByRole("button", { name: "Queue report", exact: true }).click();

  // The create call carried the queue flag and the UI says so.
  await expect.poll(() => postedBody?.queued).toBe(true);
  await expect(page.getByText(/queued; the list updates automatically/i)).toBeVisible();

  // The queued report renders with its pill and downloads disabled.
  await expect(page.locator(".pill--status-queued").first()).toBeVisible();
  await expect(
    page.getByRole("button", { name: "JSON", exact: true }).first(),
  ).toBeDisabled();

  // When the worker finishes, the fast polling flips the pill to Ready.
  status = "ready";
  await expect(page.locator(".pill--status-ready").first()).toBeVisible({ timeout: 15_000 });
  await expect(
    page.getByRole("button", { name: "JSON", exact: true }).first(),
  ).toBeEnabled();
});
