import { expect, test } from "@playwright/test";

import { API, backendEmpty, deviceA, deviceB, json, trackCrashes } from "./helpers";

test("switching device stops requests for the previous device", async ({ page }) => {
  const crashes = trackCrashes(page);
  await backendEmpty(page);
  await page.route(`${API}/devices`, route => json(route, [deviceA, deviceB]));
  const seen: string[] = [];
  page.on("request", request => {
    const match = request.url().match(/\/(?:telemetry|predictions)\/(?:latest|history|summary)\/([^/?]+)/);
    if (match) seen.push(match[1]);
  });

  await page.goto("/live");
  await expect(page.locator("select").first()).toHaveValue("PACK_A");
  await expect.poll(() => seen.includes("PACK_A")).toBe(true);

  await page.locator("select").first().selectOption("PACK_B");
  await expect(page.locator("select").first()).toHaveValue("PACK_B");
  await expect.poll(() => seen.includes("PACK_B")).toBe(true);

  // After the switch settles, nothing new should be requested for PACK_A.
  await page.waitForTimeout(500);
  const mark = seen.length;
  await page.waitForTimeout(1500);
  expect(seen.slice(mark).filter(id => id === "PACK_A")).toEqual([]);
  expect(crashes).toEqual([]);
});

test("selected device survives navigating between pages", async ({ page }) => {
  await backendEmpty(page);
  await page.route(`${API}/devices`, route => json(route, [deviceA, deviceB]));
  await page.goto("/live");
  await page.locator("select").first().selectOption("PACK_B");
  await page.getByRole("link", { name: /Battery Health/ }).click();
  await expect(page.locator("select").first()).toHaveValue("PACK_B");
});
