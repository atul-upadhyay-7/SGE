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

## Step 21 — RUL for the random-load cells, kept separate from G1

The random-load check had already found four cells outside G1 that reach retirement — B0039 (G5) and B0042, B0043, B0044 (G6) — but they were only counted, never used. New `src/rul_cross_group.py` builds their RUL labels with the same rule as G1 (the first valid discharge below 80% of the cell's first-five-median capacity) into `data/processed/rul_random_load_labels.csv`, then runs three experiments, each in its own table: (a) leave-one-cell-out inside G6, (b) the matcher trained on G1 and tested on each G6 cell, and (c) trained on G6 and tested on each G1 cell. Every table reports both methods (reference-trajectory matching and fade-rate extrapolation) and the fleet-lifetime baseline for every held-out cell. The matcher is driven by the held-out cell's own measured SOH rather than the cycle-level SOH model, because that model is defined on constant-current features and is not valid across these loads; the extrapolation reads only the held-out cell's own opening discharges, so its number is identical in all three protocols. On (a) neither method beats the baseline — the three G6 cells retire within a couple of discharges of each other, so the baseline is a very strong guess — and the report says so in one line. On (b) both methods beat the baseline on every G6 cell; on (c) the matcher beats it on the mean and on three of the four G1 cells while the extrapolation beats it on one. The stage is wired into `run_pipeline.py` after `rul_trajectory`, and new `tests/test_rul_cross_group.py` checks the labels against the independent random-load check, the reference counts and finiteness of every protocol, the (a)/(b)/(c) verdicts, the protocol-independence of the extrapolation, and that the numbers reproduce from a fresh evaluation.

---

## Step 22 — Why the two RUL methods disagree, and whether a longer window helps

The report now explains the disagreement instead of only counting it. The fade-rate extrapolation fits one straight line through a cell's first 20 measured discharges, so its answer is set by that window's slope alone. For B0005 and B0007 the opening 20 discharges barely decline, so the fitted line is too shallow: it reaches 80% at discharge 183 (B0005) and 215 (B0007) while the cells actually retire at 107 and 125 — it misses the late knee and over-predicts remaining life by 76 and 90 discharges. New `docs/make_rul_extrapolation_plot.py` draws both cells (measured SOH, the marked fit window, the extrapolated line, the true and implied end of life) into `docs/rul_extrapolation_B0005_B0007.png`, and the README states plainly what fails: a flat early fade makes the line miss the knee. The trajectory matcher never extrapolates that early slope — it reads the current level against reference curves — which is why it lands far closer on the same cells (7.1 and 17.5), and why the reverse holds on B0006, which fades from the start and defeats the matcher while the extrapolation is accurate. The second half of the step tests one more simple idea: `window_sweep()` in the same module refits the line at 20, 30 and 40 discharges and re-scores all eight labelled cells into `data/processed/rul_extrapolation_window_sweep.csv`, and the README adds the error-versus-window table in the `rul` region. The 20-discharge column reproduces the committed `extrapolation_mae` exactly, so the sweep is comparable to the tables above. The longer window is not kept: the mean falls (114.5 to 63.3 discharges) but only because the one flat-early cell (B0039, 691.7 to 25.1) stops being an outlier; the median does not fall (17.1 to 18.3) and five of the eight cells get worse, B0005 and B0007 roughly tripling (76.9 to 242.6 and 90.8 to 179.7), because a longer shallow window flattens the line further. The default window therefore stays at 20, and `tests/test_rul_cross_group.py` now also checks the sweep covers all eight cells at three windows, that its 20-discharge column equals the scored extrapolation for both G1 and G6, that the longer window does not lower the held-out median or help most cells, and that the sweep reproduces.

---

## Step 23 — B0041's 0.056 Ah reference is a measured artifact, and the rule is left alone

B0041 was excluded from every model but the report only said its "very-low-capacity runs corrupt its early reference capacity". The actual record was printed to settle why. Its first ten discharge runs carry 0.0556, 0.0557, 0.0558, 0.0561, 0.0565, 0.0441, 0.0568, 0.0569, 0.0444, 0.0446 Ah against mean currents of −0.254, −0.293, −0.259, −0.261, −0.393, −0.260, −0.264, −0.267, −0.270, −0.226 A, with the terminal voltage falling only from about 4.19 V to 4.14 V over a ~1000 s run. Ambient is the group's own 4 °C, so it is not a different condition; the load is about a quarter of the group's 1 A schedule and the cell barely discharges, so the ~0.056 Ah is a measurement failure in the opening runs, not a low-capacity cell. From cycle 104 the same cell records 1.0–1.2 Ah at ~0.7–0.85 A with the voltage dropping to ~3.5 V, which is a real G6 discharge. The reason the production rule cannot drop the bad runs is structural: it treats a discharge as invalid when it is below half the cell's median, and for B0041 the bad runs are 42 of 67 rows, so the median it normalises against (0.048 Ah) is itself the artifact and nothing is filtered. The reference is then the median of the first five of those rows, 0.056 Ah. `src/random_load.py` now records, per cell, the opening-run capacity/current/voltage-drop summary, the median capacity of the later half of the valid record, and a `reference_is_artifact` flag that is true when the reference is under half that later median; the README's random-load paragraph now states the numbers instead of asserting corruption. The rule was **not** changed: B0041 is already excluded (its SOH rises and clears the 105% ceiling), every kept cell's reference is sound, and rewriting the shared validity normaliser would retrain the model for no change in any reported decision. The regression guard is enforced by tests instead: `tests/test_data_cleaning.py` fails if any kept cell's reference is under half the median of its later valid discharges, `tests/test_random_load.py` fails if any kept random-load cell trips the same guard, and a dedicated test pins B0041 as the excluded cell that does trip it (artifact reference, opening current under half an amp, opening voltage drop under half a volt).

---

## Step 24 — README scope: what this is not, and what a BMS must provide

The README now opens its scope instead of leaving it to be inferred. A new **What this is not** section states three limits plainly: the numbers are measured on laboratory NASA cells cycled under controlled constant-current and randomised-load profiles, never on the production packs this project targets or on a customer cell; there is no field validation, no deployment on a real battery, no comparison with a manufacturer's reference and no hardware sign-off, and the dashboard and MQTT path are exercised on injected faults rather than aged field data; and the RUL is indicative rather than a forecast, because remaining-life labels need a cell that reaches the end-of-life threshold, only a few cells do, and the two methods disagree on the mean. A second section, **What a real BMS must provide**, states the three conditions a discharge must meet before the partial-window model will score it: it must cover the trained voltage band, the cell must supply the minimum number of early discharges the cell-relative baseline needs, and the discharge current must sit near the rate band the model was trained across. None of those thresholds is typed into the document. `src/features.py` gained the last missing constant — `WINDOW_TRAIN_CURRENT_LOW_A` and `WINDOW_TRAIN_CURRENT_HIGH_A`, the 2 A (G1, G9) and 4 A (G3) rates the kept cells run at — and `scripts/make_report.py` imports the module and reads the band, the minimum discharge count and the opening-baseline window straight from it, so the README states exactly what the model's own contract states and the two cannot drift. The new `bms_contract` region is rendered from those constants like every other region, so a hand-typed threshold or a stale one fails `scripts/check_numbers.py`. `tests/test_readme_scope.py` adds the guard: it fails if either section disappears, if the honesty bullet list loses its three claims, or if the rendered region disagrees with `src/features.py`.

---

## Step 25 — One end-of-life definition, shared by every table

End of life was being reported from two code paths. `src/rul_trajectory.py` located the crossing with `int(np.argmax(y <= threshold))` — a 0-based position on its smoothed curve — so `rul_trajectory_results.csv` and `rul_method_comparison.csv` put B0005 at 106, B0006 at 60, B0007 at 124 and B0018 at 77, while every other table (the cross-group tables, the random-load labels and check, `cell_audit.csv`, the window sweep, the degradation rates and the cleaned dataset) put the same crossing at 107, 61, 125 and 78. The definition now lives in one place. `src/preprocessing.py` exports `first_eol_discharge(soh, discharge_index=None, threshold=EOL_SOH_THRESHOLD)`: the first discharge, counted from 1 to match `discharge_index`, whose measured SOH is strictly below 80%, or `None` for a cell that never retires. Every producer calls it — `preprocessing.py` and `data_cleaning.py` build `eol_cycle_threshold` from it, `rul_trajectory.py` routes `evaluate_loco`, `extrapolation_loco`, `extrapolation_bands`, `method_disagreement_loco`, `calibrated_band_loco` and `_traj_points` through a small `_first_eol_position`, `rul_cross_group.py` derives `_eol_index` from it and shifts the window sweep's `implied_eol` onto the same 1-based axis, and `random_load.py` reports `eol_discharge` from it. `docs/make_rul_extrapolation_plot.py` is drawn on the same 1-based axis and its PNG was regenerated. The two trajectory tables now read 107, 61, 125 and 78 and every table agrees cell by cell, while no RUL error metric moved — trajectory MAE 7.11 / 51.06 / 17.47 / 6.48 and extrapolation 76.9 / 8.0 / 90.8 / 8.2 are unchanged, because only the reported label changed, not the fit. `tests/test_eol_definition.py` reads every table that reports an end-of-life discharge and fails if two of them disagree for the same cell, if a reported number disagrees with the value recomputed from the cleaned SOH, or if a cell that never retires is given a number.

---

## Step 26 — Does the SOH window model survive other loads?

The README already said the random-load cells are "left out of the model because their load and temperature change across the record", which reads as though the partial-window model would fail on them. That was an untested assumption. The shipped model (`models/soh_window_all`) is trained only on the nine constant-current cells (the four G1 cells, the four G3 cells and B0053), so the four random-load cells the audit keeps — B0039 (G5) and B0042, B0043, B0044 (G6) — are a genuinely held-out condition. New `src/random_load_transfer.py` feeds each of them to that shipped model with exactly the online features (`features.window_model_row`: the window made relative to the cell's own first-five-discharge baseline, plus capacity per amp) and scores the result against the production-rule SOH, with the window formula on exactly the same discharges, writing `data/processed/soh_random_load_transfer.csv`. The model does not fail: it beats the window formula on all four cells (mean MAE 2.31% versus 8.07%) and tracks the fade (R² above zero) on three of them, so the exclusion is a comparability choice, not a transfer failure. B0039, whose load changes within the record, is the exception: its R² is -0.09, below a constant mean, so there the model reads the level and not the fade shape — the same caveat B0053 already carries. `random_load.audit_cell`'s production-rule frame was factored into `random_load.clean_cell_frame` so the transfer check scores the measured SOH the audit reports rather than re-deriving it; the audit output is byte-identical. The stage runs in `run_pipeline.py` after `random_load`; `scripts/make_report.py` renders the table into the results region, `README.md` gains a Known-limits-at-a-glance row and updates the two claims that said the random-load cells were excluded because the model could not handle them, and `tests/test_random_load_transfer.py` pins the artifact, the fresh recomputation, the shipped model's exclusion of these cells, the per-cell win over the formula, and the B0039 caveat.

---

## Step 27 — Noise and shorter-window robustness, on held-out cells only

The partial-window SOH model had never been tested against the sensor noise or the window choice a real BMS would actually present it. New `src/window_robustness.py` rebuilds the window features from the raw `.mat` traces for every held-out cell, after adding Gaussian voltage noise at 5, 10 and 20 mV, Gaussian current noise at 1, 2 and 5% of each sample's current, and after narrowing the window to 3.9-3.5 V and 3.8-3.6 V. The training siblings stay clean and only the held-out cell is perturbed, so this measures inference-time robustness, not a change of what the model was trained on. `src/load_data.py` gained one shared reader, `iter_discharge_traces`, which yields the raw per-sample arrays and is now the single reader `parse_battery_file` builds its features from, so the robustness path and the offline loader see the same cycles in the same order; `_voltage_window_features` and `build_cycle_features` took optional `window_high`/`window_low` so the same extractor runs over a narrower band, with the model itself still only ever scored on the default band. `nasa_cycle_features_raw.csv` is byte-identical after the refactor. The pooled held-out MAE (all nine cells, clean training) reproduces the committed partial-transfer numbers exactly at 1.57%, and stays under the 3% budget up to the largest noise tried — 2.53% at 20 mV of voltage noise and 1.56% at 5% of current noise; current noise barely moves the features because the window capacity is an integral of the current, so zero-mean multiplicative noise averages out, and the window voltage features do not see the current at all. A shorter window does not survive: 3.9-3.5 V gives 4.99% and 3.8-3.6 V gives 6.47%, so the smallest window whose pooled MAE stays under 3% is the trained 4.0-3.4 V band itself, and the cost lands almost entirely in G1's leave-one-cell-out fold while G3 and B0053 stay near the budget. This is a training/inference band mismatch, not evidence that a model trained on a narrower band would fail. `run_pipeline.py` runs the stage after `random_load_transfer`, writing `data/processed/soh_window_robustness.csv` (per cell), `soh_window_robustness_summary.csv` (per condition) and `soh_window_robustness_limits.csv` (the two answers), `scripts/make_report.py` renders the level-by-level table, the per-protocol breakdown and the verdict into the results region, the README gains a Known-limits-at-a-glance row and Known Limitations item 9, and `tests/test_window_robustness.py` pins the artifacts, a fresh recomputation, the exact match of the clean level to the committed transfer numbers, the noise monotonicity, the two shorter windows exceeding the budget and the derived answers.

---

## Step 28 — RUL ships as a range, not a point estimate

Neither remaining-life method wins everywhere, so the project stopped presenting one. Inside G6 (leave-one-cell-out, references drawn from the cell's own group) the fleet-lifetime average is the best estimate on all three cells — MAE 1.0 to 3.0 discharges — against the trajectory matcher's 3.2 to 8.7 and the early-fade extrapolation's 5.9 to 24.6. Across groups (train G6, test G1) the matcher beats the fleet-lifetime average on three of the four G1 cells; the exception is B0006, which fades faster than any reference trajectory. `src/rul_trajectory.py` now writes that envelope into `data/processed/rul_prediction_bands.csv`: `lifetime_average_rul` and `extrapolated_rul` join the trajectory `predicted_rul` (the median over references), and `rul_low`/`rul_high` are their minimum and maximum on each discharge (mean width 91.2 discharges on the held-out G1 cells). The dashboard stopped shipping a single number: the stat tile reports the latest per-cell range, and the RUL table and time-series panels show the range with the three methods inside it, all read from those columns. `scripts/make_report.py` renders the decision paragraph into the RUL region from the cross-group and band CSVs — every number placeholdered, so it cannot drift — and `tests/test_rul_range.py` pins the envelope (low == min, high == max, each method inside), the fresh recomputation, the two cross-group claims and the paragraph. No third method was added, and the reference-spread percentiles are still stored and reported in the report; only the dashboard's headline changed.

---

## Step 29 — README cut to a scope-and-results front page, and a generated model card

The README had grown to 1250 lines and mixed the results with the setup manual, so the front page was cut to 206 lines: what the project is and is not, a known-limits table, what a real BMS must provide, a results-at-a-glance table, the known limitations, a documentation index and how to run verify. Every result table moved to `docs/RESULTS.md` (the Key Results, RUL, degradation and dataset sections) and the install/architecture/live-monitor/operations manual moved to `docs/SETUP.md`. The generated regions did not become hand-edited: `scripts/make_report.py` now routes each region to its own document — `bms_contract` and the new `results_glance` (the same body as the CHANGES `final` table) stay in the README, `results`/`rul`/`degradation`/`dataset` go to `docs/RESULTS.md`, `pack` to `docs/SETUP.md`, and a new `model_card` region to `docs/MODEL_CARD.md` — so the placeholder machinery is unchanged across all nine regions. `docs/MODEL_CARD.md` is generated, not written: the input names, units and refusal thresholds come from `src/features.py` (`WINDOW_UNITS`, `WINDOW_MODEL_FEATURES`, the window/noise/current bounds), the held-out accuracies from the partial-transfer and RUL-trajectory CSVs, and the tested noise and window levels from the robustness summary, so it restates the shipped contract and the measured held-out scores without a typed number. `docs/OWN_CELL_PROTOCOL.md` is the one hand-written page: how to log your own 18650 cells (1 Hz or faster, one row per second, monotonic seconds, three full reference discharges at a fixed current first) and the four CSV columns the loader and the streaming tracker both read. `scripts/check_numbers.py` and `scripts/verify.sh` now scan and sync the five documents, and the seven README-reading tests point at the document each section now lives in (`test_cross_condition.py` at the B0053 plot, `test_window_robustness.py`, `test_window_heldout.py`, `test_random_load_transfer.py` and `test_rul_range.py` at `docs/RESULTS.md`, `test_readme_numbers.py` at the copied `docs/` set); `test_readme_scope.py` is unchanged because both scope headings and the `bms_contract` region remain in the README.

---

## Step 30 — How partial the window really is

The documents call the 4.0–3.4 V window a "partial discharge", which is easy to read as a small early slice. It is not: for the warm G1 cells it holds most of the discharge, and only the cold G9 cell holds a minority. New `src/window_partial.py`, a pipeline stage after `window_robustness`, measures the claim from `data/processed/nasa_ml_dataset_clean.csv` instead of asserting it — per kept cell it averages `vw_capacity_ah / capacity_ah` and `vw_time_s / discharge_duration_s` over the cell's discharges and writes `data/processed/soh_window_partial.csv` (per cell plus a MEAN row). Across the nine kept cells the window holds 55.67% of each discharge's capacity and 51.45% of its time on average, ranging from 25.98% (B0053, G9, 4 °C) to 79.63% (B0007, G1): a majority for the G1 cells, about half for G3, and a quarter or less for the cold G9 cell. `scripts/make_report.py` renders a new `window_share` region — the README limits-table row "**\"Partial\" does not mean small**" — and adds a "How partial the window is" section with the per-cell table to `docs/MODEL_CARD.md`, both from those numbers, so no fraction is typed by hand. The `replace_region` helper grew one rule to support it: a region whose start marker is not at the beginning of a line is rendered inline, so a markdown table cell does not get split across lines. Two claims that implied the window was a small early stage were reworded: the model card's "first stage of a discharge" and the generated `docs/CONTRACT.md` "the cell's first stage" (via `docs/make_contract.py`). `tests/test_window_partial.py` recomputes the per-cell shares from the clean dataset, checks every share is a fraction in (0, 1], checks the MEAN row and that B0053 holds the least, and checks both documents carry the numbers.

---

## Step 31 — A shorter window, tested fairly

Step 27 scored the shipped model on narrower bands and found it fail, but that is a mismatch test: the model is trained on the 4.0–3.4 V feature distribution, so a different band at inference moves its inputs off what it learnt. The README read that as "a shorter window does not work", which the experiment never showed. New `src/window_train_sweep.py`, a pipeline stage after `window_partial`, trains a *separate* model per window — 4.0–3.4 V, the insets 3.9–3.5 V and 3.8–3.6 V, and the shifted bands 4.1–3.7 V and 3.7–3.4 V — with each band's cell-relative features rebuilt from the raw `.mat` traces over that band alone (`features.window_model_row`, the same arithmetic the online estimator uses), and scores them the same way the shipped model is scored: leave-one-cell-out within G1 and within G3, then train on every G1 and G3 cell and test B0053 (G9) and the four random-load cells (G5/G6). All thirteen scored cells are held out of their own training set. It writes `data/processed/soh_window_train_sweep.csv` (per cell), `_summary.csv` (per window) and `_limits.csv` (the answer). With its own model every band holds the pooled held-out MAE under 3% (constant-current cells: 1.47–2.22%); the smallest is the 3.8–3.6 V inset (span 0.20 V) at 1.94% pooled over all thirteen held-out cells and 1.56% over the nine constant-current cells, against 1.70% for the shipped 4.0–3.4 V band. The random-load cells are the stricter subset — there only 4.0–3.4 V and 3.9–3.5 V stay under budget (3.8–3.6 V reaches 3.33%) — and ElasticNet blows up on B0039, whose opening discharges barely cross the band and give a tiny five-discharge baseline, so a linear fit extrapolates to implausible SOH where the tree model stays bounded; the 3% question is therefore read against XGBoost, the shipped model family. A row's window inputs that are NaN (a discharge that never entered the band) are dropped before scoring rather than imputed. `scripts/make_report.py` renders a new `docs/RESULTS.md` subsection ("A model trained on its own window") and a reworked README limits row (an inline `window_train` region), and the old "does not survive a shorter window" paragraph in the robustness section was rewritten to name it a mismatch and point at the fair test. `tests/test_window_train_sweep.py` checks the trained band reproduces the offline `cell_relative_features` path exactly, that every window is scored for both models on all thirteen cells, that every window survives with its own model, that the smallest survivor is the 3.8–3.6 V inset, and that both documents carry the result.

---

## Step 32 — Can charging estimate SOH?

The question was whether the constant-current charge can estimate SOH the way the 4.0–3.4 V discharge window does. First answer: the loader never kept charge data — `load_data.iter_discharge_traces` skips every cycle whose type is not a discharge, so the ML dataset, the window model and every document see discharges alone (B0005 has 170 charges in the `.mat`, none in the dataset). Following the phase instruction, charge loading was added as a *separate* stage rather than by changing the discharge path: `src/charge_soh.py` has its own `iter_charge_traces` and a rising-band window extractor (the discharge extractor walks the band down; a charge walks it up), so no existing output moves. It reads the 3.8–4.0 V constant-current charge band — the upper bound is 4.0 V, not the 4.1 V one might first try, because several charges on B0053, B0032 and B0039 top out near 4.08 V and a 4.1 V band would drop those cells from the protocol — rebuilds the same five window quantities (time, capacity, mean voltage, dQ/dV peak) and the same `features.window_model_row` cell-relative ratios, pairs each charge with the discharge that follows it for the production-rule SOH target, and runs the same held-out protocol (LOCO G1+G3, B0053, random-load). Result: charging carries a real SOH signal but is not a replacement for the discharge window. On G3 the charge model matches the discharge model (XGBoost 1.35% vs 1.07%), but it fails the 3% budget on G1 (3.23% vs 1.57%), on B0053 (12.72% vs 2.99%) and on the random-load cells (8.04% vs 2.18%): the charge window transfers worse to the colder and unseen-load cells. Outputs `data/processed/soh_charge_window.csv` and `soh_charge_window_comparison.csv`; a new `charge` region renders the comparison table and verdict in `docs/RESULTS.md`. `tests/test_charge_soh.py` counts both readers against the raw `.mat` cycle list (so the "loader drops charge" claim is checked, not asserted), checks the discharge column is the same held-out task as the discharge sweep's trained band, and pins the G3-matches/G1-B0053-random-load-lose pattern.

## Step 33 — Freeze `v1.0-nasa`

Froze this build as `v1.0-nasa` on the verified commit and stopped adding methods. Everything the tag claims comes from the NASA lab cells in the audited dataset, is produced by the committed pipeline, and is reproduced by `scripts/verify.sh` on the tagged commit. No new method may land after this tag until our own cell data exists.

**Validated (NASA lab cells).** One thing per cell only:

- The SOH and RUL models, the window model, the partial-window analysis, the per-window trained models and the charge-window comparison, each on the nine audit-kept NASA cells plus the four random-load cells, under the held-out protocol stated on each result.
- Every number in `README.md`, `CHANGES.md`, `docs/RESULTS.md`, `docs/SETUP.md` and `docs/MODEL_CARD.md` is rendered from a CSV by `scripts/make_report.py` and re-checked by `scripts/check_numbers.py`; the exact CSV path, group split, model family and held-out protocol are on each result rather than assumed.
- Documentation-vs-code consistency: `scripts/verify.sh` re-runs the pipeline from the committed tree in a clean clone and fails on any drift.

**Not validated (our own packs).** Nothing about a pack or a cell we own:

- No field data and no customer cell: every result is a lab cell in a controlled chamber, not a pack in service.
- No variable/real-load pack: the random-load cells are NASA cells replayed under the imported load, which is a stress test of transfer, not validation on our hardware.
- No hardware sign-off: no ESP32/battery-hardware run, no pack-level thermal or safety validation.
- No external alerting path: the alert rule is delivered to a local mock receiver, not a real email/pager endpoint.
- The RUL band is a reference spread, not a calibrated interval, and has not been recalibrated (the per-cell failure is reported rather than hidden).

## Step 34 — Reproducible metrics: round to ten decimals, flag the unstable

`verify.sh` passed on the freezing machine but the clean-tree check could fail elsewhere: `data/processed/soh_window_train_sweep.csv` carried an ElasticNet R² of −464498 for the random-load cell B0039, and a value of that magnitude is dominated by floating-point summation order, so the last digits differ from one machine or thread count to the next and a fresh clone writes `−464498.041216769` where the committed file held `−464498.0412167689`. Rounding to ten decimals was already applied to every written frame (`src/repro.py:rounded`, used by every stage that writes a CSV), but rounding cannot rescue a number whose ninth decimal already depends on the box. The fix has two halves. First, `src/repro.py` gains `stable_metric(value, low, high)`, which rounds a written metric to ten decimals and replaces it with the word `unstable` when it leaves a sane range; because it rounds the value *before* it can mix with the flag, it covers the object-typed column that `DataFrame.round` can no longer reach. Second, the metric-writing stages apply it with the thresholds the phase set — R² below −1000 or MAE above 100% — in `window_train_sweep.py` (per-cell rows, and the pooled values, which propagate `unstable` rather than average a non-measurement), `window_robustness.py` and `charge_soh.py`; `make_report.py` renders the flag as a bare *unstable* cell (`metric_ph`) and the documents explain it. RUL MAE, measured in discharges rather than percent, is deliberately left alone: the 100% ceiling is a percentage rule. `tests/test_window_train_sweep.py` adds a test that runs the stage twice on a trimmed workload and fails unless the written bytes are identical, plus one that fails if any committed score leaves the sane range without being flagged; `tests/test_charge_soh.py` checks the same for the charge-versus-discharge comparison.

## Step 35 — Judge every window on three held-out numbers, not the pool

The earlier window verdict read a single pooled number, and the pool hid the failures. `soh_window_train_sweep_summary.csv` now carries, per band, the pooled held-out MAE, the MAE on the four random-load cells alone and the worst single held-out cell (`xgb_mae_worst_cell_pct`), plus the three pass flags and their conjunction `under_limit`; `soh_window_train_sweep_limits.csv` records `n_under_limit` and the smallest passing band under the conjunction. A band passes only when all three are under the 3% budget, and on the committed data **none does**: all five bands hold the pool, two (`4.0–3.4 V`, `3.9–3.5 V`) also hold the random-load mean, and none keeps every cell under budget — the shipped `4.0–3.4 V` band's worst cell is B0039 at 3.64% against 1.70% pooled. So the earlier "a shorter window works if trained on it" survives on the pooled and random-load numbers but not on the worst cell, and the documents now say so. In the same pass the headline tables drop ElasticNet: `docs/RESULTS.md` shows the three XGBoost numbers as the headline, states that XGBoost is the model, and moves ElasticNet and the window formula into an appendix that notes the instability; the README claim row states the three-number verdict. `tests/test_window_train_sweep.py` pins the conjunction, the worst-cell column and the document wording.

## Step 36 — The estimator refuses a model input outside its trained range

ElasticNet's blow-ups on a random-load cell were the visible symptom of a wider hole: the estimator scored whatever it was handed, even an input the model had never seen. `src/train_window_soh.py` now records the per-feature `[min, max]` each model input was trained across in the bundle (`models/soh_window_all.json`, and the held-out bundle), written through `train_soh._save_portable` and carried by `predict.load_portable_bundle`. `src/features.py` gains `WINDOW_RANGE_SLACK` (0.10) with `widen_trained_range` and `out_of_range_features`, which widen the training interval by the slack fraction of its span on each side and name every input that leaves it. The backend `predict_soh` checks the cell-relative inputs against that widened range and returns the sentinel `OUT_OF_TRAINED_RANGE` rather than a scored number; `cycle_service._predictions` treats the sentinel as a refused cycle, so a discharge the model has no basis for is left without a prediction instead of storing garbage. `tests/test_window_model_features.py` pins the recorded ranges to the training data, the widening arithmetic, and a feature set at three times the training maximum; `backend/tests/test_window_estimator.py` checks the estimator returns the sentinel for an input at three times the training maximum and that the sentinel is a single shared constant. `docs/MODEL_CARD.md` names the rule under its refusal rules.

---

## Final performance & baseline comparison

<!-- make_report:final -->
| Task | Method | Protocol | Score | Baseline | Unit |
|---|---|---|---|---|---|
| SOH (offline, full features) | Coulomb formula | leave-one-cell-out (G1) | 0.73 | constant mean 9.28 | MAE % |
| SOH (offline, full features) | XGBoost+ElasticNet blend (nested) | nested leave-one-cell-out (G1) | 1.89 | constant mean 9.28 | MAE % |
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
