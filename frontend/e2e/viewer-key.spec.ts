import { expect, test } from "@playwright/test";

import { API, backendEmpty, deviceA, json } from "./helpers";

const KEY = "e2e-viewer-key";

async function lockedBackend(page: import("@playwright/test").Page) {
  await backendEmpty(page);
  const streamHeaders: Array<string | undefined> = [];
  await page.route(`${API}/**`, route => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    const key = route.request().headers()["x-viewer-key"];
    if (path === "/health") {
      return json(route, { status: "ok", access_control: { viewer_key: true } });
    }
    if (path === "/stream") {
      streamHeaders.push(key);
      if (key !== KEY) return json(route, { detail: "nope" }, 401);
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: 'data: {"kind":"telemetry","payload":{}}\n\n',
      });
    }
    if (key !== KEY) return json(route, { detail: "nope" }, 401);
    if (path === "/devices") return json(route, [deviceA]);
    return json(route, { items: [], total: 0, count: 0, limit: 50, offset: 0 });
  });
  return streamHeaders;
}

test("locked dashboard asks for the viewer key and rejects a wrong one", async ({ page }) => {
  await lockedBackend(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Viewer key required" })).toBeVisible();
  await expect(page.getByRole("navigation")).toHaveCount(0);

  await page.getByLabel("Viewer key", { exact: true }).fill("wrong");
  await page.getByRole("button", { name: "Unlock" }).click();
  await expect(page.getByText("That key was not accepted.")).toBeVisible();
  await expect(page.getByRole("navigation")).toHaveCount(0);
});

test("the right viewer key unlocks, reads and the stream carry it, nothing persisted", async ({ page }) => {
  const streamHeaders = await lockedBackend(page);
  await page.goto("/");
  await page.getByLabel("Viewer key", { exact: true }).fill(KEY);
  await page.getByRole("button", { name: "Unlock" }).click();
  await expect(page.getByRole("navigation")).toBeVisible();
  await expect(page.locator("select").first()).toHaveValue("PACK_A");
  await expect.poll(() => streamHeaders.includes(KEY)).toBe(true);
  expect(await page.evaluate(() => JSON.stringify(localStorage))).toBe("{}");
});

test("a key rejected mid-session sends the user back to the prompt", async ({ page }) => {
  await lockedBackend(page);
  await page.goto("/");
  await page.getByLabel("Viewer key", { exact: true }).fill(KEY);
  await page.getByRole("button", { name: "Unlock" }).click();
  await expect(page.getByRole("navigation")).toBeVisible();
  // Key rotated on the server: the next read is refused.
  await page.route(`${API}/anomalies**`, route => json(route, { detail: "nope" }, 401));
  await expect(page.getByRole("heading", { name: "Viewer key required" })).toBeVisible({ timeout: 15_000 });
});
