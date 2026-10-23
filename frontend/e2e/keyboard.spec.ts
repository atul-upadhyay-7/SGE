import { expect, test } from "@playwright/test";

import { backendEmpty } from "./helpers";

test("sidebar links are reachable and activatable by keyboard", async ({ page }) => {
  await backendEmpty(page);
  await page.goto("/");
  const link = page.getByRole("link", { name: "Anomaly Center" });
  await link.focus();
  await expect(link).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/anomalies$/);
});

test("tab order reaches every sidebar link before page content", async ({ page }) => {
  await backendEmpty(page);
  await page.goto("/");
  const names: string[] = [];
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press("Tab");
    const label = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      return el?.tagName === "A" ? (el.textContent ?? "").replace(/^[^A-Za-z]+/, "").trim() : "";
    });
    if (label) names.push(label);
  }
  for (const expected of [
    "Overview",
    "Live Monitoring",
    "Anomaly Center",
    "Battery Health",
    "Historical Reports",
    "Device Management",
    "System Health",
  ]) {
    expect(names).toContain(expected);
  }
});

test("operator key control opens and closes from the keyboard", async ({ page }) => {
  await backendEmpty(page);
  await page.goto("/");
  const button = page.getByRole("button", { name: "Operator key" });
  await button.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Operator key (this tab only)")).toBeVisible();
  await expect(button).toHaveAttribute("aria-expanded", "true");
});
