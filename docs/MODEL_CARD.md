# Model card — partial-window SOH and indicative RUL

<!-- generated from src/features.py and the result CSVs by scripts/make_report.py; do not edit by hand -->
<!-- regenerate: python scripts/make_report.py -->

This card is generated, not written. The inputs, units and refusal
thresholds are read from `src/features.py`; the accuracy numbers are
read from `data/processed/experiments/soh_partial_transfer.csv`,
`data/processed/rul_trajectory_results.csv` and
`data/processed/soh_random_load_transfer.csv`; the tested noise and
window levels are read from `data/processed/soh_window_robustness_*.csv`.
A hand-typed number in the card fails `scripts/check_numbers.py`.

<!-- make_report:model_card -->
## What it predicts

Three things from measurements a battery management system can actually take. A **partial-window SOH** (percent) from the 4.0–3.4 V window alone — the majority of a discharge's capacity on the warm cells and about a quarter on the cold one, not a small early slice — read before the discharge finishes. An **indicative remaining life** in sequential discharges, reported as a range, not a point. And the **window formula** it is scored against, the best a BMS can compute from the window alone.

## How partial the window is

Calling this a "partial discharge" is not a claim that the window is small. Averaged over the 9 kept cells the 4.0–3.4 V window holds 55.7% of each discharge's capacity and 51.4% of its time. It is not uniform: the warm G1 cells hold most of their capacity in the window, while the cold G9 cell traverses the band early and holds little.

| Cell | Capacity inside the window (%) | Time inside the window (%) |
|---|---|---|
| B0005 | 76.43 | 68.84 |
| B0006 | 62.38 | 56.04 |
| B0007 | 79.63 | 75.74 |
| B0018 | 74.59 | 67.91 |
| B0029 | 49.60 | 47.39 |
| B0030 | 46.46 | 42.70 |
| B0031 | 49.83 | 48.69 |
| B0032 | 36.09 | 35.35 |
| B0053 | 25.98 | 20.36 |
| **Mean over 9 cells** | 55.67 | 51.45 |

## Inputs and units

The raw quantities measured inside the trained window, from `src/features.py` (`WINDOW_FEATURES` plus the one normalisation input).

| Input | Unit | Meaning |
|---|---|---|
| `vw_time_s` | seconds | seconds spent inside the window |
| `vw_capacity_ah` | ampere_hours | charge delivered inside the window |
| `vw_voltage_mean` | volts | mean cell voltage inside the window |
| `vw_dqdv_peak_v` | volts | voltage of the dQ/dV peak inside the window |
| `vw_dqdv_peak_height` | ampere_hours_per_volt | height of the dQ/dV peak |
| `current_mean` | amperes | mean current over the discharge; the one non-window input the cell-relative normalisation needs |

## Model inputs

The estimator takes the raw window made relative to the cell's own opening median of the first 5 discharges, so the model reads a level, not an absolute capacity:

- `vw_capacity_ratio`
- `vw_capacity_per_amp`
- `vw_time_ratio`
- `vw_voltage_ratio`
- `vw_dqdv_height_ratio`

## Trained on

Only the nine constant-current NASA cells the audit keeps — G1 (B0005, B0006, B0007, B0018), G3 (B0029–B0032) and G9 (B0053). Target is `soh` (percent). The random-load cells are scored but never trained on.

## Held-out accuracy

The partial-window SOH model is scored by leave-one-cell-out inside G1 and G3, then trained on G1+G3 and tested on the single G9 cell. **MAE is in percentage points of SOH, on a cell the model never saw.**

| Held-out cell | Protocol | Cell-relative XGBoost MAE (%) |
|---|---|---|
| B0005 | leave-one-cell-out: G1 | 1.06 |
| B0006 | leave-one-cell-out: G1 | 3.31 |
| B0007 | leave-one-cell-out: G1 | 0.88 |
| B0018 | leave-one-cell-out: G1 | 0.89 |
| B0029 | leave-one-cell-out: G3 | 0.69 |
| B0030 | leave-one-cell-out: G3 | 1.29 |
| B0031 | leave-one-cell-out: G3 | 0.65 |
| B0032 | leave-one-cell-out: G3 | 1.64 |
| B0053 | train G1+G3, test B0053 | 2.99 |

Indicative RUL, from the reference-trajectory matcher scored leave-one-cell-out over the four G1 cells. **MAE is in sequential discharges.**

| Held-out cell | Trajectory matcher MAE (discharges) |
|---|---|
| B0005 | 7.1 |
| B0006 | 51.1 |
| B0007 | 17.5 |
| B0018 | 6.5 |

## Conditions tested

- Constant-current discharges at the three kept operating conditions: G1 24 °C / 2 A, G3 43 °C / 4 A, G9 4 °C / 2 A.
- The 4 random-load cells the audit keeps (B0039, B0042–B0044), scored with the shipped model but never trained on.
- Sensor noise added to a held-out cell's inputs: Gaussian voltage noise up to 20 mV and Gaussian current noise up to 5% of each sample's current.
- The trained window and two narrower windows: 4.0–3.4 V (trained), 3.9–3.5 V and 3.8–3.6 V (tested at inference).
- A model trained on each candidate window (4.0–3.4, 3.9–3.5, 3.8–3.6, 4.1–3.7, 3.7–3.4 V): judged on three held-out numbers at once — pooled MAE, random-load MAE and the worst single cell — no band holds all three under 3%; the shipped 4.0–3.4 V band's worst cell is 3.64% against 1.70% pooled.

## Conditions NOT tested

- Real packs: no field data, no customer cell, no manufacturer reference and no hardware sign-off.
- Temperatures and currents outside the three tested conditions and the trained current band, which is 2.0–4.0 A.
- Duty cycles with rapid, continuous load changes beyond the random-load cells above; only one current level per condition is trained on.
- Multi-cell pack interactions (imbalance, thermal coupling); the live pack monitor is exercised on injected synthetic faults.
- The ESP32 firmware on real hardware, and an external email or pager endpoint — the alert delivery target is a local mock receiver.

## Refusal rules

The estimator returns no number, rather than a guess, when:

- a window is measured outside the trained 4.0–3.4 V band (`features.WindowRangeError`);
- fewer than 3 opening discharges are available for the cell-relative baseline (`InsufficientReferenceError`);
- the discharge current sits far outside the trained 2.0–4.0 A band, where the model has no training data;
- any cell-relative model input leaves the range the model was trained across, widened by 10% of its span: the estimator returns the message *out of trained range* instead of a number, because the model has no basis outside that range.
<!-- make_report:model_card:end -->
