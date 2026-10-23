import { expect, test } from "@playwright/test";

import { API, backendEmpty, json } from "./helpers";

test("a refused write explains the operator key instead of failing silently", async ({ page }) => {
  await backendEmpty(page);
  await page.route(`${API}/reports`, route =>
    route.request().method() === "POST"
      ? json(route, { detail: "missing or invalid 'X-Operator-Key' header" }, 401)
      : json(route, { items: [], total: 0, count: 0, limit: 50, offset: 0 }),
  );
  await page.goto("/reports");
  await page.getByRole("button", { name: "Generate report", exact: true }).click();
  await expect(page.getByText(/needs the operator key/i)).toBeVisible();
});

test("the operator key is sent on writes, kept out of localStorage", async ({ page }) => {
  await backendEmpty(page);
  let header: string | undefined;
  await page.route(`${API}/reports`, route => {
    if (route.request().method() === "POST") {
      header = route.request().headers()["x-operator-key"];
      return json(route, { detail: "stop" }, 400);
    }
    return json(route, { items: [], total: 0, count: 0, limit: 50, offset: 0 });
  });
  await page.goto("/reports");
  await page.getByRole("button", { name: "Operator key", exact: true }).click();
  await page.getByLabel("Operator key (this tab only)").fill("e2e-key-value");
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByRole("button", { name: "Generate report", exact: true }).click();
  await expect.poll(() => header).toBe("e2e-key-value");
  expect(await page.evaluate(() => JSON.stringify(localStorage))).toBe("{}");
});
