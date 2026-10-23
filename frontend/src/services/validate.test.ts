import { expect, it } from "vitest";

import { isValidationReport } from "./validate";

const REPORT = {
  schema_version: 1,
  generated_at: "2026-10-23T00:00:00+00:00",
  source: "lab_sample_nasa",
  dataset: "sample_input.csv",
  model: {
    name: "soh_window_all",
    artifact: "models/soh_window_all.ubj",
    algorithm: "XGBoost",
    live: true,
  },
  summary: {
    total_rows: 6,
    reference_rows: 5,
    scored_rows: 1,
    refused_rows: 0,
    mae_pct: 1.0,
    rmse_pct: 1.0,
    bias_pct: 1.0,
    max_abs_error_pct: 1.0,
    refusals_by_reason: {},
  },
  offline_benchmark: null,
  notes: [],
  rows: [
    {
      cell_id: "B0005",
      discharge_index: 6,
      actual_soh_pct: 90,
      predicted_soh_pct: 91,
      error_pct: 1,
      abs_error_pct: 1,
      status: "scored",
      refusal_reason: null,
    },
  ],
};

it("accepts a well-shaped validation report", () => {
  expect(isValidationReport(REPORT)).toBe(true);
});

it("rejects a report with non-numeric metrics or missing rows", () => {
  expect(
    isValidationReport({
      ...REPORT,
      summary: { ...REPORT.summary, mae_pct: "1.0" },
    }),
  ).toBe(false);
  expect(isValidationReport({ ...REPORT, rows: null })).toBe(false);
  expect(isValidationReport(null)).toBe(false);
});
