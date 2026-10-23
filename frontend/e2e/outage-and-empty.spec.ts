import { expect, test } from "@playwright/test";

import { backendEmpty, backendOffline, ROUTES, trackCrashes } from "./helpers";

for (const route of ROUTES) {
  test(`offline backend: ${route} keeps the shell and shows no crash`, async ({ page }) => {
    const crashes = trackCrashes(page);
    await backendOffline(page);
    await page.goto(route);
    await expect(page.getByRole("navigation")).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
    // Give polling/async loads time to settle into their error states.
    await page.waitForTimeout(800);
    await expect(page.locator("main, .page").first()).not.toBeEmpty();
    expect(crashes).toEqual([]);
  });

  test(`empty backend: ${route} renders without a crash`, async ({ page }) => {
    const crashes = trackCrashes(page);
    await backendEmpty(page);
    await page.goto(route);
    await expect(page.getByRole("navigation")).toBeVisible();
    await page.waitForTimeout(800);
    await expect(page.locator("main, .page").first()).not.toBeEmpty();
    expect(crashes).toEqual([]);
  });
}

test("empty backend says no devices are registered", async ({ page }) => {
  await backendEmpty(page);
  await page.goto("/");
  await expect(page.getByText("No devices registered")).toBeVisible();
});

test("offline backend says devices are unavailable, not empty", async ({ page }) => {
  await backendOffline(page);
  await page.goto("/");
  await expect(page.getByText("Devices unavailable")).toBeVisible();
  await expect(page.getByText("No devices registered")).toHaveCount(0);
});
