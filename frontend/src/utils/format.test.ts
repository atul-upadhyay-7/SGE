import { expect, it } from "vitest";
import { formatHourBucket } from "./format";
it("treats naive hourly API buckets as UTC", () => {
  expect(formatHourBucket("2026-10-22T15:00:00")).toBe(formatHourBucket("2026-10-22T15:00:00Z"));
});
it("preserves half-hour local timezone offsets instead of replacing minutes with :00", () => {
  const d = new Date("2026-10-22T15:00:00Z");
  expect(formatHourBucket("2026-10-22T15:00:00")).toBe(
    `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`,
  );
});
