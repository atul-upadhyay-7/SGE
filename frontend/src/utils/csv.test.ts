import { describe, expect, it } from "vitest";

import { toCsv } from "@/utils/csv";

const columns = [{ key: "v", header: "Value" }];

function cell(value: unknown): string {
  return toCsv(columns, [{ v: value }]).split("\r\n")[1];
}

describe("toCsv formula-injection guard", () => {
  it("neutralises formula-leading string cells", () => {
    expect(cell("=2+5")).toBe("'=2+5");
    expect(cell("+SUM(A1:A9)")).toBe("'+SUM(A1:A9)");
    expect(cell("@cmd")).toBe("'@cmd");
    expect(cell("-HYPERLINK(\"http://x\")")).toContain("'-HYPERLINK");
  });

  it("leaves genuine numbers untouched", () => {
    expect(cell(-3.2)).toBe("-3.2");
    expect(cell("-3.2")).toBe("-3.2");
    expect(cell(42)).toBe("42");
  });

  it("still applies RFC 4180 quoting", () => {
    expect(cell('a,"b"')).toBe('"a,""b"""');
    expect(cell(null)).toBe("");
  });
});
