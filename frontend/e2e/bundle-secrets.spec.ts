import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

// The production bundle must carry public config only: no tokens, DB or
// admin credentials, and no operator key value.
const FORBIDDEN: [string, RegExp][] = [
  ["GitHub token", /github_pat_[A-Za-z0-9_]{20,}|ghp_[A-Za-z0-9]{20,}/],
  ["private key", /-----BEGIN [A-Z ]*PRIVATE KEY-----/],
  ["DB URL with password", /postgres(ql)?(\+\w+)?:\/\/[^:\s"']+:[^@\s"']+@/],
  ["Grafana admin default", /GF_SECURITY_ADMIN_PASSWORD|admin:admin/],
  ["backend key setting", /BMS_(ADMIN|OPERATOR)_API_KEY\s*[=:]\s*["'][^"']+["']/],
];

test("built bundle contains no secrets", () => {
  const dir = join(process.cwd(), "dist", "assets");
  const files = readdirSync(dir).filter(name => /\.(js|css|html)$/.test(name));
  expect(files.length).toBeGreaterThan(0);
  for (const file of files) {
    const text = readFileSync(join(dir, file), "utf8");
    for (const [label, pattern] of FORBIDDEN) {
      expect(pattern.test(text), `${label} found in ${file}`).toBe(false);
    }
  }
});
