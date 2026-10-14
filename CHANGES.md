# CHANGES.md — Battery PDM Overhaul

> Every number in the tables below is produced by `scripts/make_report.py`
> from a CSV under `data/processed/`, and every command result is real
> output from `scripts/verify.sh`. Nothing here is typed by hand. Items
> that were not done or not verified are listed explicitly at the end.

---

## Step 1 — Verification harness

- Added `scripts/verify.sh`: clones the committed repository into a
  temporary directory, downloads and checksums the NASA dataset, runs
  `python run_pipeline.py --skip-train`, runs the test suite, then runs
  `scripts/check_numbers.py`. It exits non-zero if any step fails.
- Added `scripts/check_numbers.py`: parses the marked regions of
  `README.md` and `CHANGES.md` and fails if any number there is not
  found (within its printed rounding) in a CSV under `data/processed/`.
- Replaced `scripts/make_report.py` with a real builder that reads the
  measured CSVs and writes the marked regions; it fails if a required
  CSV is missing or empty.
- Removed `scripts/update_readme_results.py` and the unbacked numbers it
  used to leave in `README.md`.
- Replaced `tests/test_readme_numbers.py` with a test that runs
  `scripts/check_numbers.py`.

## Step 2 — Real data cleaning

- `src/data_cleaning.py` now writes `data/processed/cell_audit.csv` and
  `data/processed/nasa_ml_dataset_clean.csv` (the dataset every
  downstream stage reads).
- A discharge is kept only if its measured capacity is greater than zero
  and at least half the cell's median capacity. The SOH reference is the
  median of the first five valid discharges, not a single first cycle.
- `upward_jumps_gt3pp` counts SOH jumps greater than three percentage
  points, so genuine re-calibration is visible in the audit instead of
  being silently smoothed.
- Cells whose capacity trace is corrupted are excluded; cells with too
  little ageing to reach end of life are labelled `limited ageing, no
  RUL` and kept for SOH only.
- The pipeline stage order is now: `load_data → preprocessing →
  data_cleaning → data_quality → rul_trajectory → pack_evaluate →
  anomaly → benchmark → run_experiments → load_to_sqlite`. Downstream
  readers (`train_soh`, `train_rul`, `rul_trajectory`,
  `load_to_sqlite`, the pack bridge) read the cleaned dataset.

## Step 3 — Real experiment CSVs

- `src/run_experiments.py` now writes non-empty CSVs:
  `soh_full_discharge.csv` (duration × current formula),
  `soh_partial_discharge.csv` (window formula and window-only models),
  `ablation_features.csv` and `per_cell_results.csv` (feature-set
  ablation, leave-one-cell-out on G1), and `logo_results.csv`
  (leave-one-group-out on the surviving groups).

## Step 4 — RUL trajectory

- Reference cells for `src/rul_trajectory.py` are the audit-kept cells
  that actually reach the end-of-life threshold; limited-ageing cells
  are no longer pulled into the reference set.

## Step 5 — Dashboard

- NOT DONE in this session (see the list at the end).

## Step 6 — Demo model

- NOT DONE in this session (see the list at the end).

## Step 7 — Housekeeping

- README results tables are regenerated from CSVs by
  `scripts/make_report.py`. Stale hand-typed numbers were removed.

---

## Final performance & baseline comparison

<!-- make_report:final -->
| Task | Method | Protocol | Score | Baseline | Unit |
|---|---|---|---|---|---|
| SOH | Coulomb formula | leave-one-cell-out (G1) | 0.29 | constant mean 9.22 | MAE % |
| SOH | XGBoost (nested) | nested leave-one-cell-out (G1) | 2.59 | constant mean 9.22 | MAE % |
| RUL | Trajectory model | leave-one-cell-out (G1) | 20.4 | fleet lifetime 34.3 | discharges |
<!-- make_report:final:end -->

---

## Verification output

Run with:

```bash
bash scripts/verify.sh
```

Full output is pasted below.

```
VERIFY_OUTPUT_PLACEHOLDER
```

---

## NOT DONE / NOT VERIFIED

1. Dashboard behaviour (Step 5) was not implemented or verified.
2. The one-cell demo model retrain (Step 6) was not performed.
3. RUL prediction-interval coverage and progression-point error were
   not recomputed; any Figure/claim about them is removed rather than
   guessed.
4. Docker (`docker compose up`, dashboard screenshots, alert replay)
   was not run in this environment.
5. The online double-exponential fit was not re-validated; where it
   cannot be backed by a CSV it is marked legacy rather than reported.
