# CHANGES.md — Battery PDM Overhaul

> Every number in the tables below is produced by `scripts/make_report.py`
> from a CSV under `data/processed/`, rendered as a placeholder, and
> checked by `scripts/check_numbers.py`; the verification output at the
> end is real output from `scripts/verify.sh`. Nothing here is typed by
> hand. Each step is one paragraph; items that were not done or not
> verified are listed explicitly at the end.

---

## Step 1 — Verification harness

Added `scripts/verify.sh` (clones the committed repository into a temp dir, downloads and checksums the NASA dataset, runs `python run_pipeline.py --skip-train`, runs the test suite, then `scripts/check_numbers.py`, exiting non-zero on any failure) and `scripts/check_numbers.py`; replaced `scripts/make_report.py` with a real builder that reads the measured CSVs, writes the marked regions and fails on a missing or empty required CSV; removed `scripts/update_readme_results.py` and its unbacked numbers, and rewrote `tests/test_readme_numbers.py` to drive the checker.

## Step 2 — Real data cleaning

`src/data_cleaning.py` now writes `data/processed/cell_audit.csv` and `data/processed/nasa_ml_dataset_clean.csv` (the dataset every downstream stage reads): a discharge is kept only if its measured capacity is positive and at least half the cell's median, the SOH reference is the median of the first five valid discharges rather than a single first cycle, `upward_jumps_gt3pp` counts SOH jumps above three percentage points so re-calibration is visible instead of smoothed, corrupted cells are excluded, low-ageing cells are kept for SOH only (`limited ageing, no RUL`), and the pipeline order is `load_data → preprocessing → data_cleaning → data_quality → rul_trajectory → pack_evaluate → anomaly → benchmark → run_experiments → load_to_sqlite`.

## Step 3 — Real experiment CSVs

`src/run_experiments.py` now writes non-empty `soh_full_discharge.csv` (duration × current formula), `soh_partial_discharge.csv` (window formula and window-only models), `ablation_features.csv` and `per_cell_results.csv` (feature-set ablation, leave-one-cell-out on G1), and `logo_results.csv` (leave-one-group-out on the surviving groups).

## Step 4 — RUL trajectory reference cells

Reference cells for `src/rul_trajectory.py` are the audit-kept cells that actually reach the end-of-life threshold; limited-ageing cells are no longer pulled into the reference set.

## Step 5 — Dashboard honesty

Removed every hardcoded fallback from `dashboard/grafana_api.py` (each stat tile reads the database, or returns an explicit error row when a source table is missing, instead of a plausible-looking constant); added real RUL prediction intervals (`data/processed/rul_prediction_bands.csv`, per held-out cell and discharge the 10th/50th/90th percentiles of the per-reference-cell estimates, replacing the fixed ±20% fake); measured that band's empirical coverage (48.3% across the four held-out cells, not the ~80% its label implied) and relabelled it a reference spread; computed `model_summary` from the result CSVs; redefined `stat_discharges_left` from the stored percentiles; chained the `cell` variable to `group`; removed the stale result numbers; and extended `tests/test_grafana_api.py` to pin the band ordering, the chaining and the panel filtering.

## Step 6 — Demo model retrain

Retrained the SOH and RUL models on the audited clean dataset, because the committed models predated the cleaning pass and the reported numbers came from the pre-cleaning data; `soh_xgb_demo` trains without the replayed cells (B0006) so the live replay is genuinely out of sample, and the README/CHANGES values were regenerated from the retrained CSVs (e.g. Coulomb baseline 0.29 → 0.73).

## Step 7 — Housekeeping

README results tables are regenerated from the CSVs by `scripts/make_report.py`, and stale hand-typed numbers were removed.

## Step 8 — Dashboard under Docker

`docker compose --profile demo up --build` was run against the full stack (Mosquitto, the SOH services, the pack bridge, the JSON API and Grafana); all six containers reached a running state with both provisioned dashboards at `http://localhost:3050`, after adding an explicit container `dns` (without it the Docker embedded resolver forwards to the host's unreachable `127.0.0.53` stub and Grafana exits on first boot), and the run was verified end to end through the JSON datasource proxy with refreshed screenshots in `docs/`.

## Step 9 — Machine-checked report numbers

`scripts/make_report.py` now renders every number in the marked regions as a `{{id}}` placeholder from `data/processed/report_numbers.json` (a flat id→value map plus the region templates under a reserved `_templates` key), `scripts/check_numbers.py` re-renders each region from the JSON and fails on any difference or on a bare number that is not a placeholder (so a number cannot reach the documents by being typed into them), the shared machinery lives in `scripts/report_render.py`, `tests/test_readme_numbers.py` proves the checker fails on a bumped, inserted or pasted number, and `scripts/verify.sh` runs pytest as an explicit step and covers the JSON in its sync check.

## Step 10 — Cross-condition transfer, faced (superseded by Step 15)

The leave-one-group-out table became the first table under *Key Results*, opened by the plain statement that raw cycle features do not transfer across conditions (ElasticNet mean MAE 42.26% G1, 20.69% G3, 47.61% B0053, against 1.81% leave-one-cell-out within G1); `src/run_experiments.py:cross_condition` then rebuilt the features as per-cell baseline-relative quantities using only a cell's own opening discharges, recovering most of the gap (1.37%/1.34%/1.67%) while the Coulomb formula still beat every model on the two multi-cell groups and the model won only on the single cross-condition cell B0053 (labelled `B0053 (G9)` and never averaged into a group mean). Step 15 supersedes the full-cycle numbers with the window task; the old numbers are in git history.

## Step 11 — The RUL interval is a reference spread, and named so

Renamed the RUL 10th/90th interval to a **reference spread** everywhere it appears (README table columns, `report_summary.csv` labels, Grafana panel titles, API table columns) so nothing calls it a prediction or confidence interval, and stated what it is: the percentiles of the remaining-life estimates from the reference cells a curve is matched against, measuring dependence on the reference rather than containment of the truth; its measured coverage is 48.3% across the four held-out cells, with B0006 at 0.0% because it fades faster than every reference cell (0.309 %/discharge) so the matcher over-predicts its remaining life — reported rather than hidden.

## Step 12 — Grafana alerts: real structure, data-driven bound (superseded by Step 14)

Rewrote both provisioned rule files as three-node pipelines (`A query → B reduce(last) → C threshold`, `condition: C`), shortened the rule window from 24 h to 1 h, replaced the hand-typed 55 C bound with the maximum peak temperature the SOH model trained on (63.0242 C) written by `scripts/make_alert_rules.py` (a pipeline stage) into both provisioning copies, and diagnosed that the rules never fired because the `simpod-json-datasource` plugin declares `"alerting": false` and `"backend": false`, so Grafana's alerting engine reports `plugin.unavailable` for every query on any version.

## Step 13 — Repository size, documented rather than rewritten

`.git` is about 226 MB because the NASA `.mat` dataset (34 unique blobs, ~198.5 MB), the model artifacts (72 versions, ~69.1 MB) and four DB versions (~4.0 MB) were committed early and stay in history even though they are ignored now (`*.mat`, `BMS Dataset/`, `data/raw/`); added `.gitattributes` marking `.png/.mat/.db/.joblib/.ubj` as `binary` (they already contain CRLF bytes, so a text/eol rule would renormalise and dirty the tree) and `data/processed/**` and `models/**` as `linguist-generated`, and noted that a fresh clone downloads all of it and shrinking would need `git filter-repo`, which this project deliberately does not run.

## Step 14 — Grafana alerts are evaluated by Grafana itself

Fixed the Step 12 caveat instead of documenting it: added `dashboard/alert_exporter.py` (a standard-library Prometheus exporter serving the latest per-cell gauges from `data/live_pack.db`), added Prometheus (`docker/prometheus/prometheus.yml` with a 5 s scrape and `exporter` + `prometheus` services), provisioned a second datasource (**Battery PDM Prometheus**, uid `battery-pdm-prom`) in both provisioning trees, rewrote `scripts/make_alert_rules.py` so the query node targets Prometheus and `noDataState`/`execErrState` are `Alerting`, deleted the old `scripts/check_alerts.py` and its test (a re-implementation proves nothing about Grafana), and added `scripts/grafana_alerts.py` (reads Grafana's own rules API) plus `tests/test_alert_datasource.py` (fails if a rule queries a plugin whose `plugin.json` has `"alerting": false`); on the live stack after an injected thermal fault both rules report `health: ok` and `state: firing`, one instance per failing cell (SOH on CELL1, temperature on CELL3).

## Step 15 — SOH honesty: full discharge is a formula, the window is the model

Removed the claim that the model competes with Coulomb counting (a full discharge measures capacity, so SOH is read by a formula; the partial window is the only signal a BMS has online and is the task every SOH model is scored on), stated that at the top of the README and fixed the Evaluation Protocol and Known Limitations item 2; in `src/run_experiments.py` made `cell_relative_features` take a `ref_window`, switched `cross_condition`/`logo_results.csv` to window features and the window formula, and added `partial_discharge_transfer` and `reference_window_sensitivity` (new `soh_partial_transfer.csv`, `soh_partial_reference_window.csv`); raw window features do not transfer (ElasticNet 42.66% G1, 16.29% G3, 7.47% B0053) but cell-relative ones make the model beat the window formula on every held-out group (4.58% vs 10.92% G1, 1.60% vs 2.13% G3, 3.42% vs 10.15% B0053) including B0006 (2.51%/3.31% vs 18.82%), and the reference-window table is reported as measured; updated `scripts/make_report.py`, `tests/test_cross_condition.py` and the module docstring.

## Step 16 — RUL: a second method, and a calibrated band that is not possible

Added `extrapolation_loco` to `src/rul_trajectory.py` (fits a straight line through the first `EXTRAP_WINDOW = 20` measured SOH values of a cell and extrapolates to the threshold, using only observable history) and scored it on the same held-out discharges; trajectory matching wins the mean and three of four cells (mean MAE 20.5 vs 46.0; B0005 7.1 vs 76.9, B0007 17.5 vs 90.8) while extrapolation wins only the fast linear fader B0006 (8.0 vs 51.1), so trajectory matching stays the default and the loss is reported; added `calibrated_band_loco`, which calibrates the 10th/90th offsets from the other three cells and measures held-out coverage — it swings from 0.0% (B0006) to 100.0% (B0005, B0018) at a 57.0% mean, not fixable with only three calibrating cells, confirming the reference spread as the only supportable band; `scripts/make_report.py` gained the two required CSVs and tables, and README Known Limitations item 5 records both results.

## Step 17 — The cell filter is checked against a gentler re-reference

Added a data-quality study to `src/data_cleaning.py` — `reference_capacity` names the rules, `exclusion_reason` applies the unchanged ceiling and capacity-rise tests, and `reference_sensitivity` recomputes every cell's decision under each rule, shared with the production pass so the rules cannot drift (new `data_reference_sensitivity.csv`, pinned by `tests/test_data_reference.py`); the gentler median-3-8 rule returns none of the excluded cells, the beginning-of-life peak returns exactly the four G7 cells (B0054-B0056 stay out because their capacity rises from the recorded first discharge, and B0054 also above the ceiling) and no G4 cell returns under any rule; the decision is reported and not adopted — `REFERENCE_RULE` stays `first5_median`, the kept set stays at nine cells, no reported number moves — with a "re-referencing the excluded cells" table, Known Limitations item 7, and Dataset prose that no longer calls the excluded cells "corrupted integrations".

---

## Step 18 — RUL, honestly: indicative, both methods, and cells beyond G1

`src/rul_trajectory.py` now factors the fade-rate extrapolation into a shared `_implied_eol` (so the estimate that is scored and the one the dashboard draws cannot drift), adds `extrapolation_bands` (per held-out cell and discharge, the extrapolation's implied remaining life, `data/processed/rul_extrapolation_bands.csv`) and `method_disagreement_loco` (per held-out cell, the mean and 90th-percentile absolute gap between the trajectory estimate and the extrapolation on the same discharges, `data/processed/rul_method_disagreement.csv`); the report now labels RUL **indicative**, shows both methods side by side on every held-out cell instead of keeping the extrapolation as a token loser, and reports the disagreement (63.8 discharges on the mean, 107.1 on B0007) as the uncertainty rather than choosing a per-cell winner. A new `src/random_load.py` runs the NASA random-load groups G5 (B0038-B0040) and G6 (B0041-B0044) through the production SOH rule and reports that 5 of 7 reach 80% and 4 (B0039, B0042, B0043, B0044) also pass the keep filter, so the "only four G1 cells reach 80%" claim is corrected to "four more cells do"; B0041 reaches 80% but is excluded because the very-low-capacity runs its notes flag corrupt its early reference capacity. `src/random_load.py` is a pipeline stage, `scripts/make_report.py` reads both new CSVs, and README (Known-limits row, RUL header, RUL Model section and Known Limitations item 5), `docs/RESEARCH.md` and this file were updated.

---

## Step 19 — Backend and ML contract, generated and shrink documented

Added `docs/CONTRACT.md`, a backend and ML interface contract generated by the new `docs/make_contract.py` from the single source `src/features.py`: the six raw window inputs with their units (`WINDOW_FEATURES` + `WINDOW_NORMALIZATION_INPUTS`), the five cell-relative model inputs in bundle order (`WINDOW_MODEL_FEATURES`) with their definitions, the trained voltage band and the reference-window/minimum-discharge parameters, the model files (`models/soh_window_all.ubj`, `.joblib`, `.json`, whose feature list the generator checks against `WINDOW_MODEL_FEATURES`), the `predict_soh` estimator payload and its two refusals (`WindowRangeError`, `InsufficientReferenceError`), a pointer to the separate backend telemetry payload (`backend/app/schemas/telemetry.py`), and the offline `FEATURES`/`LEAKY_FEATURES` lists; the generator fails rather than emitting a blank row if a model input has no description, and new `tests/test_contract_doc.py` re-renders the document in memory and fails if the committed copy drifts, if a window feature or unit is missing, or if the model-file, payload or refusal names disappear. README Setup gained a "Repository size" subsection documenting that `.git` is about 226 MB because the dataset and early artifact commits stay in history, and that shrinking it is a separate, user-approved `git filter-repo` step this project does not run and `scripts/verify.sh` does not depend on; the docs paragraph now links the contract.

---

## Step 20 — The window model's B0005 number is held out, not in-sample

The B0005 fixture the backend was pinned to was produced by the deployable model, which was trained on every audited cell including B0005. Reproducing it therefore only proved the estimator runs the same deterministic pipeline, not how well the model predicts a cell it has never seen. That distinction is now explicit. `src/train_window_soh.py` renamed its per-discharge output to `data/processed/soh_window_regression_B0005.csv` and labels it an in-sample regression check, not accuracy; the backend test that reads it (`backend/tests/test_window_estimator.py`) says so and points at where the accuracy number lives. The script now also trains `soh_window_no_B0005` on B0005's own operating group minus B0005 — the other G1 cells B0006, B0007 and B0018, the report's leave-one-cell-out: G1 split — and writes `data/processed/soh_window_heldout_B0005.csv` for that cell. Its held-out MAE is 1.06% (XGBoost) and 1.34% (ElasticNet), the same numbers the report's B0005 row shows. New `tests/test_window_heldout.py` holds the artifact to that: it recomputes the held-out mean error and fails unless it matches the leave-one-cell-out: G1 B0005 row to within 0.01%, checks the held-out bundle's `trained_on` excludes B0005 while `soh_window_all`'s includes it (which is why the deployable error is in-sample), and fails if the README ever quotes the in-sample figure as accuracy. The README partial-discharge section now names the held-out B0005 row as the accuracy number for the deployable estimator and describes the in-sample file as a determinism check.

---

## Final performance & baseline comparison

<!-- make_report:final -->
| Task | Method | Protocol | Score | Baseline | Unit |
|---|---|---|---|---|---|
| SOH (offline, full features) | Coulomb formula | leave-one-cell-out (G1) | 0.73 | constant mean 9.28 | MAE % |
| SOH (offline, full features) | XGBoost (nested) | nested leave-one-cell-out (G1) | 2.65 | constant mean 9.28 | MAE % |
| SOH (partial window) | Cell-relative XGBoost | train G1+G3, test B0053 | 2.99 | window formula 10.15 | MAE % |
| RUL | Trajectory model | leave-one-cell-out (G1) | 20.5 | fleet lifetime 34.3 | discharges |
<!-- make_report:final:end -->

---

## Verification output

Run with:

```bash
bash scripts/verify.sh
```

Real output for the Step 1–17 work (commit `c1d51fd`); the per-stage
table dumps and the download progress meter are elided, nothing else is:

```text
============================================================
verify.sh
  repository : /home/harsh/Documents/battery-pdm
  python     : Python 3.12.3
  workdir    : /tmp/tmp.nV0bUnRoNb
============================================================

>>> cloning committed repository
    HEAD: c1d51fd

>>> downloading and checksumming the NASA dataset
...
Verifying SHA256 checksums ...
Checksum verification succeeded: all files in checksums.sha256 match.

>>> running the pipeline (skip training)
  STAGE: load_data
  STAGE: preprocessing
  STAGE: data_cleaning
  STAGE: data_quality
  STAGE: rul_trajectory
  STAGE: run_experiments
  STAGE: pack_evaluate
  STAGE: anomaly
  STAGE: make_report
  STAGE: make_alerts
  STAGE: load_to_sqlite
  STAGE: pytest
  229 passed in 11.52s
All stages completed.

>>> checking the pipeline left the working tree clean
    git status is clean after the pipeline

>>> running the test suite
  229 passed in 10.39s

>>> checking numbers in README.md / CHANGES.md
all 345 numbers in 6 region(s) are rendered placeholders of 315 values from
/tmp/tmp.nV0bUnRoNb/clone/data/processed/report_numbers.json

>>> checking generated documents are in sync
    README.md, CHANGES.md and report_numbers.json match the generator output

============================================================
verify.sh: ALL CHECKS PASSED
============================================================
```

---

## NOT DONE / NOT VERIFIED

1. The native systemd-user deployment (`battery-pdm-api` / `battery-pdm-grafana`) and the Docker stack both bind ports 8099 and 3050, so only one can run at a time. Docker was used for the run above; the systemd units are the lighter alternative.
2. The RUL 10th–90th band is the spread of the per-reference-cell estimates, not a calibrated interval. It is now named a reference spread everywhere (Step 11) and its empirical coverage is measured and reported (48.3% across the four held-out cells), including the B0006 mechanism that makes it 0.0% there. It has deliberately not been recalibrated to hit a target coverage: with four cells a conformal band would be marginal and would hide, rather than fix, the per-cell failure.
3. The online double-exponential fit was not re-validated; where it cannot be backed by a CSV it is marked legacy rather than reported.
4. Grafana alerts are now evaluated by Grafana's own engine (Step 14) through the Prometheus datasource, and the firing rule is delivered to a provisioned webhook contact point — a local mock receiver (`alert-sink`) that records each notification; `scripts/verify_alerts.sh` fails unless a rule fires *and* the delivery lands. What is still not verified: an external email/pager endpoint, real pack data, variable-load validation and ESP32 hardware.
