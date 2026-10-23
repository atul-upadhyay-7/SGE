/**
 * Model-validation endpoint (PoC validation slice).
 *
 * Serves the latest report written by scripts/validate_real_data.py,
 * which scores cycles through the live XGBoost estimator. A 404 means
 * no validation run exists yet — the page renders an honest empty
 * state, never invented metrics.
 */
import { apiFetch } from "@/services/api";
import { isValidationReport } from "@/services/validate";
import type { ValidationReport } from "@/types/domain";

export function getValidationSummary(
  signal?: AbortSignal,
): Promise<ValidationReport> {
  return apiFetch<ValidationReport>("/validation/summary", {
    signal,
    validate: isValidationReport,
  });
}
