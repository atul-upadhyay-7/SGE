# Backend and ML contract

<!-- generated from src/features.py by docs/make_contract.py; do not edit by hand -->
<!-- regenerate: python docs/make_contract.py -->

This document is generated, not written: `docs/make_contract.py`
reads the canonical definitions from `src/features.py` and
`models/soh_window_all.json` and renders them here. Feature
names, units and the model input list therefore have exactly one
source, and `tests/test_contract_doc.py` fails if this committed
copy drifts from it.

## Partial-window inputs

The partial-window SOH model scores one discharge from the
window a BMS can always see — the 4.0 V down to
3.4 V span every discharge crosses
(`WINDOW_VOLTAGE_HIGH` / `WINDOW_VOLTAGE_LOW`), which is most
of a warm cell's capacity and not a small early slice — plus
the cell's opening discharges. A
window measurement outside that band is refused rather than
scored (see *Refusals* below).

| Input | Unit | Meaning |
|---|---|---|
| `vw_time_s` | seconds | seconds spent inside the 4.0-3.4 V window |
| `vw_capacity_ah` | ampere_hours | charge delivered inside the window |
| `vw_voltage_mean` | volts | mean cell voltage inside the window |
| `vw_dqdv_peak_v` | volts | voltage of the IC (dQ/dV) peak inside the window |
| `vw_dqdv_peak_height` | ampere_hours_per_volt | height of the IC peak |
| `current_mean` | amperes | mean current over the discharge; the one non-window input the cell-relative normalisation needs |

## Model inputs

The estimator consumes the raw window made relative to the
cell's own opening baseline, not the raw level. These are the
model inputs, in the order the bundle stores them
(`WINDOW_MODEL_FEATURES`):

| Model input | Definition |
|---|---|
| `vw_capacity_ratio` | window `vw_capacity_ah` over the cell's opening median of `vw_capacity_ah` |
| `vw_capacity_per_amp` | window `vw_capacity_ah` divided by `abs(current_mean)` (scale-free; removes the 2 A versus 4 A rate difference) |
| `vw_time_ratio` | window `vw_time_s` over the cell's opening median of `vw_time_s` |
| `vw_voltage_ratio` | window `vw_voltage_mean` over the cell's opening median of `vw_voltage_mean` |
| `vw_dqdv_height_ratio` | window `vw_dqdv_peak_height` over the cell's opening median of `vw_dqdv_peak_height` |

The baseline is the median of the cell's first
`WINDOW_REFERENCE_WINDOW` = 5 valid discharges of each quantity; a cell with fewer than
`WINDOW_MIN_REFERENCE_DISCHARGES` = 3 opening discharges
is refused, because the baseline would be a guess.

## Model file

| Property | Value |
|---|---|
| Portable model | `models/soh_window_all.ubj` (UBJ) |
| Joblib copy | `models/soh_window_all.joblib` |
| Bundle metadata | `models/soh_window_all.json` |
| Input features | `vw_capacity_ratio`, `vw_capacity_per_amp`, `vw_time_ratio`, `vw_voltage_ratio`, `vw_dqdv_height_ratio` |
| Target | `soh` (percent) |
| Trained on | `B0005`, `B0006`, `B0007`, `B0018`, `B0029`, `B0030`, `B0031`, `B0032`, `B0053` |
| Library versions | python 3.12.3, scikit-learn 1.7.1, xgboost 3.4.0 |

The three files are one model: the UBJ is what the estimator
loads, the joblib is the reference serialisation the trainer
writes, and the JSON carries the metadata above. Its feature
list must equal `WINDOW_MODEL_FEATURES`; if it does not, the
model was trained on a different contract.

## Estimator payload

`backend/app/ml/estimator.py::predict_soh` is the backend entry
point. It takes the window for the discharge being scored and
the cell's opening discharges. A minimal payload, with
illustrative values:

```json
{
  "window_features": {
    "vw_time_s": 1234.5,
    "vw_capacity_ah": 0.62,
    "vw_voltage_mean": 3.71,
    "vw_dqdv_peak_v": 3.66,
    "vw_dqdv_peak_height": 0.012,
    "current_mean": -2.0
  },
  "early_discharges": [
    {
      "vw_time_s": 1234.5,
      "vw_capacity_ah": 0.62,
      "vw_voltage_mean": 3.71,
      "vw_dqdv_peak_v": 3.66,
      "vw_dqdv_peak_height": 0.012,
      "current_mean": -2.0
    }
  ]
}
```

* `window_features` carries exactly the six names in the
  *Partial-window inputs* table above (`WINDOW_FEATURES` plus
  `WINDOW_NORMALIZATION_INPUTS`), with units as listed.
* `early_discharges` is the cell's opening discharges in order;
  at least 3 are required, and the baseline is the median of the first
  5 of them.
* The response is one number: the estimated SOH in percent.

### Refusals

* `features.WindowRangeError` — `vw_voltage_mean` is present
  but outside 3.4 to 4.0 V (with 0.001 V slack for
  sensor resolution). A window measured over a different span
  has no training data and is never scored.
* `estimator.InsufficientReferenceError` — fewer than
  3 opening discharges were supplied.

## Backend ingestion payload (reference)

The telemetry contract the backend ingests is separate from the
ML estimator above and is defined in
`backend/app/schemas/telemetry.py` (`TelemetryPayload`), not in
`src/features.py`. It is reproduced here only so the two
interfaces are documented in one place; that module remains its
source of truth:

| Field | Type | Notes |
|---|---|---|
| `schema_version` | int >= 1 | payload schema version |
| `message_id` | string | idempotency key |
| `device_id` | string | registered device |
| `measured_at` | datetime | must be timezone-aware (UTC) |
| `current` | float | amperes (discharge negative) |
| `temperature` | float | degrees Celsius |
| `cell_voltages` | list[float] | non-empty and finite |
| `pack_voltage` | float or null | optional |

Unknown fields are preserved in `additional_data` rather than
rejected. A legacy single-cell frame (a `voltage` and no
`cell_voltages`) is adapted onto this contract.

## Full-discharge feature set (offline model)

The offline SOH model trains on a wider list, `features.FEATURES`.
It is listed here for completeness; only the window inputs above
have units defined in `src/features.py`, and only the window
model is exposed by the backend.

* `ambient_temperature`
* `voltage_mean`
* `voltage_min`
* `voltage_max`
* `voltage_std`
* `voltage_range`
* `current_mean`
* `current_min`
* `current_max`
* `current_std`
* `temperature_mean`
* `temperature_min`
* `temperature_max`
* `temperature_std`
* `discharge_duration_s`
* `voltage_start`
* `voltage_end`
* `voltage_drop`
* `resistance_proxy_ohm`
* `vw_time_s`
* `vw_capacity_ah`
* `vw_voltage_mean`
* `vw_dqdv_peak_v`
* `vw_dqdv_peak_height`
* `load_a`
* `last_load_a`
* `delta_load_a`
* `prev_capacity_change_ah`
* `d_voltage_mean`
* `d_voltage_end`
* `d_voltage_drop`
* `d_discharge_duration_s`
* `d_resistance_proxy_ohm`
* `d_temperature_mean`
* `sl_voltage_mean`
* `sl_voltage_end`
* `sl_voltage_drop`
* `sl_discharge_duration_s`
* `sl_resistance_proxy_ohm`
* `sl_temperature_mean`

These columns are target-derived or forward-looking and are
never model inputs (`features.LEAKY_FEATURES`); `make_xy`
refuses to build a matrix that contains them:

* `soh`
* `soh_rated`
* `capacity_fade_pct`
* `soh_change_pct`
* `capacity_change_ah`
* `capacity_ah`
* `reference_capacity_ah`
* `rul_cycles`
* `rul_cycles_80`
* `eol_cycle_threshold`
* `eol_cycle_observed`
* `is_pre_eol`
* `soh_slope_pct_per_cycle`
* `soh_slope_early_pct_per_cycle`
