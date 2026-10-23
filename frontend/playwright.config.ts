import { defineConfig } from "@playwright/test";

// Browser regression suite. It runs against the *production build* served by
// `vite preview`, with the backend mocked per test, so it needs no database.
// PW_CHROME_PATH lets a machine without Playwright's browser download use an
// installed Chrome; CI installs Chromium instead.
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: "http://127.0.0.1:4173",
    launchOptions: process.env.PW_CHROME_PATH
      ? { executablePath: process.env.PW_CHROME_PATH, args: ["--no-sandbox"] }
      : {},
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run preview -- --host 127.0.0.1 --port 4173 --strictPort",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
