# SGE — Battery Predictive Maintenance System

An end-to-end machine-learning pipeline and interactive monitoring dashboard for predicting the **State of Health (SOH)** and **Remaining Useful Life (RUL)** of lithium-ion batteries. Built on the NASA Battery Dataset, the system trains per-cell and global XGBoost models using a rigorous leave-one-cell-out cross-validation protocol and serves results through a native **Grafana** dashboard backed by a custom Flask JSON API and SQLite database.

---

## Table of Contents

- [Key Results](#key-results)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Setup & Installation](#setup--installation)
- [Usage / Workflow](#usage--workflow)
- [Dashboard](#dashboard)
- [Dataset](#dataset)
- [Model Details](#model-details)
- [Technologies](#technologies)

---

## Key Results

### SOH Prediction (State of Health)

| Metric | Value |
|--------|-------|
| Model | XGBoost (nested hyperparameter search) |
| Validation | Leave-one-cell-out cross-validation |
| **Mean MAE** | **3.40%** |
| Mean RMSE | 3.86% |
| Mean R² | 0.84 |

**Per-cell breakdown (nested, honest):**

| Cell | MAE (%) | RMSE (%) | R² |
|------|---------|----------|----|
| B0005 | 2.59 | 3.21 | 0.90 |
| B0006 | 7.32 | 7.70 | 0.61 |
| B0007 | 1.79 | 2.16 | 0.94 |
| B0018 | 1.91 | 2.35 | 0.92 |

Top 3 predictive features: `voltage_mean`, `temperature_std`, `current_std`

> **Leakage note.** Earlier versions of this project included
> `soh_change_pct` and `capacity_change_ah` as model inputs.
> Both are target-derived: `capacity_ah` is the numerator of
> the SOH target, and both are first differences of it. They
> cannot be computed for a cell the model has not already
> seen, so they were removed. The headline MAE moved from the
> previously reported 3.23% to 3.40% under the re-run nested
> search; at fixed default hyperparameters the change is
> slightly *better*, not worse (mean LOCO MAE 3.3514 with
> `soh_change_pct`, 3.2712 without). The 3.40% figure is the
> leakage-free nested result and is the only one this project
> stands behind.


### RUL Prediction (Remaining Useful Life)

| Metric | Value |
|--------|-------|
| Target | Cycles remaining until 80% SOH |
| Best Model | Linear (with cycle feature dropped) |
| **Mean MAE** | **79.27 cycles** |

> **Note:** With only four cells, RUL prediction is fundamentally limited by data coverage. The model cannot outperform a constant predictor (oracle cell mean MAE: 77.78 cycles) because each cell's degradation trajectory is unique and the training set provides only three examples of how a cell ages. The best model is 1.49 cycles *worse* than that constant — it is being reported because it is the best of the eight model/feature-set combinations tried, not because it is good. `rul_results.csv` carries both feature sets (`drop` = `(none)` vs `cycle`) so the comparison is reproducible.
>
> This number also moved when the target-derived columns were removed from `FEATURES`: the RUL model shares the SOH feature list, so the earlier 85.97 no longer applies. It improved to 79.27, which is coincidental rather than meaningful — the honest read is that RUL is at the noise floor either way.

### Battery Degradation Characteristics

| Cell | EOL Cycle | Fade Rate (%/cycle) | Early Fade (%/cycle) |
|------|-----------|---------------------|----------------------|
| B0005 | 356 | 0.0567 | 0.0184 |
| B0006 | 202 | 0.0875 | 0.0887 |
| B0007 | 445 | 0.0499 | 0.0216 |
| B0018 | 185 | 0.1001 | 0.1297 |

---

## SOH Definition

SOH is defined explicitly and in exactly one place, per cell:

```
SOH = capacity_ah / reference_capacity_ah x 100
```

`reference_capacity_ah` is the **first measured discharge
capacity of that cell** (`groupby("cell_id")["capacity_ah"]
.transform("first")`, in `src/preprocessing.py`). The policy is
deliberately per-cell and first-cycle, not a chemistry-level
nominal capacity, because the NASA cells differ in their
beginning-of-life capacity. Every value in the `soh` column is
computed from that one rule.

Consequences of this policy, stated plainly:

- SOH starts at exactly 100% for every cell by construction.
- SOH is only comparable *within* a cell, since two cells with
  different first-cycle capacities share a scale but not an
  absolute reference.
- A cell that was already partly aged when first measured would
  read 100% at that point. None of the four selected cells is
  in that category.

The target is **not clipped**. `data_quality_report.json` records
its range (56.69% to 100.0%) and a test suite asserts the range
stays logical. If an impossible value ever appeared (negative, or
above 100), the audit reports it rather than hiding it.

### Two SOH concepts, kept separate

**Mode A — reference / ground-truth SOH.** The label above,
computed from measured full-cycle capacity. This is the value the
model is trained to predict, and it is only available offline,
after a full discharge has been measured and integrated.

**Mode B — predictive / BMS SOH.** What a deployed battery
management system can actually compute at cycle time: a
prediction from the measured voltage, current, temperature and
duration features of the cycle. It uses **no** SOH, no capacity,
and no future cycle. This is the only mode the streaming service
serves.

Every predictive result in this README is Mode B. Mode A appears
only as the training target.

---

## Leakage Prevention

Target-derived columns are forbidden from reaching any estimator,
and the rule is enforced in code rather than by convention:

- `src/evaluation.py` defines `LEAKY_FEATURES`, the canonical
  list. `src/train_rul.py` keeps a narrower list because RUL is
  legitimately defined against the EOL cycle.
- `src/features.py` refuses to build an `X` matrix that contains
  any column in its `LEAKY_FEATURES`. This is the single choke
  point every training script passes through.
- `evaluation.assert_no_leakage()` is called inside
  `loco_evaluate()`, so a leaky column fails the run even if a
  caller bypasses `make_xy`.
- `tests/test_leakage.py` injects each leaky column in turn and
  asserts the pipeline raises.

The list currently covers: `soh`, `capacity_fade_pct`,
`soh_change_pct`, `capacity_change_ah`, `capacity_ah`,
`reference_capacity_ah`, `rul_cycles`, `rul_cycles_80`,
`eol_cycle_threshold`, `eol_cycle_observed`, `is_pre_eol`,
`soh_slope_pct_per_cycle`, `soh_slope_early_pct_per_cycle`.

The full-lifetime slopes are the clearest temporal leak: they are
fitted over the entire degradation curve, so at cycle *t* they
already encode cycles after *t*. They are never predictive inputs.

`src/data_quality.py` fails loudly on a missing or malformed
mandatory column, naming the file, the operation, the reason and
a suggested fix.

---

## Architecture

```
┌──────────────┐    ┌──────────────────┐    ┌───────────────┐
│  NASA .mat   │───>│  load_data.py    │───>│  Raw cycle    │
│  files       │    │  (parser)        │    │  features CSV │
└──────────────┘    └──────────────────┘    └───────┬───────┘
                                                    │
                                           ┌────────▼────────┐
                                           │ preprocessing.py │
                                           │ + features.py    │
                                           └────────┬────────┘
                                                    │
                                           ┌────────▼────────┐
                                           │  ML dataset CSV  │
                                           │  (34 columns)    │
                                           └───┬─────────┬───┘
                                               │         │
                                    ┌──────────▼──┐  ┌───▼──────────┐
                                    │ train_soh.py│  │ train_rul.py │
                                    │ (XGBoost)   │  │ (Linear/XGB) │
                                    └──────┬──────┘  └──────┬───────┘
                                            │                │
                                     ┌──────▼────────────────▼───────┐
                                     │     models/ (.joblib)         │
                                     │     data/processed/ (results) │
                                     └──────────────┬───────────────┘
                                                    │
       ┌────────────────────────────────────────────▼────────┐
       │     Grafana dashboard + Flask JSON API (no feeder): │
       │                                                     │
       │     battery_pdm.db ← dashboard/load_to_sqlite.py    │
       │             │                                       │
       │       grafana_api.py  ← :8099 (datasource)          │
       │             │                                       │
       │       Grafana (provisioning/dashboards/*.json)      │
       │       http://localhost:3050 — Battery PDM (Grafana) │
       │                                                     │
       │       SOH/RUL degradations, predicted-vs-actual,    │
       │       model summaries, anomaly residuals/fade-rate   │
       │       + events. The live heartbeat monitor below is  │
       │       the MQTT ingest that feeds future readings.    │
       └─────────────────────────────────────────────────────┘

### Data heartbeat and monitoring (the live path)

The `src/streaming/` package is the live counterpart to the
offline pipeline. It ingests per-second samples, assembles them
into the same cycle-level features `load_data` builds, latches
the latest SOH reading, and republishes it on MQTT:

```
src/streaming/cycle_tracker.py   per-second samples → cycle feature rows
src/streaming/soh_service.py     latched SOH, ~0.8 s one-time load, ~2.5 ms per prediction
src/streaming/mqtt_client.py     MQTT transport (needs a broker)
src/streaming/replay.py          replays .mat files straight into the live path, no device
```

The latched reading is republished every second as a heartbeat so
a subscriber's staleness estimate (`age_s`) keeps moving:

```
battery_pdm.db → grafana_api.py (:8099) → Grafana dashboard (:3050)
```
`SOH degradation`, `Predicted vs Actual`, and the `Model Summary`
panel are static behind that path; `Anomaly: Residual/Fade rate`
+ `Anomaly Events` are the triage panels for the monitoring
operator. Data-hungry dev tests live in `tests/` (`pytest`).
```

---

## Project Structure

```
├── data/
│   ├── raw/                           # NASA Battery .mat files (B0005–B0018)
│   └── processed/
│       ├── nasa_cycle_features_raw.csv    # Extracted cycle-level features
│       ├── nasa_ml_dataset.csv            # Final ML-ready dataset (636 × 34)
│       ├── soh_results.csv                # SOH model per-cell scores
│       ├── soh_baselines.csv              # Baseline model comparison
│       ├── soh_nested_results.csv         # Nested CV XGBoost results
│       ├── soh_feature_importance.csv     # Feature importance rankings
│       ├── soh_bias_report.csv            # Prediction bias analysis
│       ├── soh_null_baselines.csv         # Constant predictor baselines
│       ├── soh_predictions_*.csv          # Per-cell SOH predictions
│       ├── rul_results.csv                # RUL model scores
│       ├── rul_degradation_rates.csv      # Cell degradation characteristics
│       ├── rul_null_baselines.csv         # RUL constant baselines
│       ├── rul_predictions_*.csv          # Per-cell RUL predictions
│       ├── data_quality_report.csv/.json  # Data audit (cells, rows, fade, duplicates)
│       └── soh_latency_benchmark.csv      # Measured inference latency
├── models/
│   ├── soh_xgb_*.joblib                  # SOH models (per-cell + global)
│   └── rul_xgb_*.joblib                  # RUL models (per-cell + global)
├── src/
│   ├── load_data.py                      # .mat file parser → raw features
│   ├── preprocessing.py                  # Data cleaning, target computation
│   ├── data_quality.py                   # Dataset audit, fails loudly on a bad table
│   ├── features.py                       # Feature list, extraction, leakage guards
│   ├── evaluation.py                     # Shared scoring and LOCO protocol
│   ├── train_soh.py                      # SOH model training pipeline
│   ├── train_rul.py                      # RUL model training pipeline
│   ├── benchmark_latency.py              # Inference latency measurement
│   ├── predict.py                        # Shared model loading and inference
│   ├── anomaly.py                        # Anomaly detection: LOCO residual + capacity event + fade-rate knee
├── dashboard/
│   ├── load_to_sqlite.py                 # Loads processed CSVs to SQLite
│   ├── grafana_api.py                    # Flask API for Grafana JSON datasource
│   └── provisioning/                     # Ran with Grafana ≥ 13.x as GF_PATHS_PROVISIONING; see "Run the whole thing" below.
├── notebooks/                            # Jupyter notebooks for EDA
├── run_pipeline.py                       # Runs all nine stages in dependency order
├── requirements.txt
└── README.md
```

---

## Setup & Installation

### Prerequisites

- Python 3.10+
- pip

### 1. Clone the repository

```bash
git clone https://github.com/atul-upadhyay-7/SGE.git
cd SGE
```

### 2. Create a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate    # Linux / macOS
# venv\Scripts\activate     # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Download the data

Download the [NASA Battery Dataset](https://www.nasa.gov/content/prognostics-center-of-excellence-data-set-repository) and place the `.mat` files (`B0005.mat`, `B0006.mat`, `B0007.mat`, `B0018.mat`) into `data/raw/`.

---

## Usage / Workflow

Run the complete pipeline in sequence:

### Step 1 — Data Loading

Parse raw `.mat` files and extract cycle-level discharge features:

```bash
python src/load_data.py
```

**Output:** `data/processed/nasa_cycle_features_raw.csv`

### Step 2 — Preprocessing & Feature Engineering

Clean data, compute SOH/RUL targets, and assemble the ML-ready dataset:

```bash
python src/preprocessing.py
```

**Output:** `data/processed/nasa_ml_dataset.csv` (636 rows × 34 columns)

### Step 3 — Audit the data

```bash
python src/data_quality.py
```

**Outputs:**
- `data/processed/data_quality_report.csv` — flat metric/value table
- `data/processed/data_quality_report.json` — the same report, structured

Reports cells, rows, cycles per cell, missing counts, duplicate
cycle ids, monotonicity violations, and total capacity fade per
cell. It raises `DataQualityError` — naming the file, the
operation, the reason and the fix — on a missing file, a missing
mandatory column, or a non-numeric mandatory column, rather than
coercing the problem away. Its reports are written next to the
dataset it was given, so auditing a fixture cannot overwrite the
real report.

### Step 4 — Train SOH Model

Train XGBoost with nested leave-one-cell-out hyperparameter search:

```bash
python src/train_soh.py
```

**Outputs:**
- `models/soh_xgb_*.joblib` — Trained model artifacts
- `data/processed/soh_*.csv` — Results, baselines, predictions, feature importance

### Step 5 — Train RUL Model

Evaluate multiple models for remaining useful life prediction:

```bash
python src/train_rul.py
```

**Outputs:**
- `models/rul_xgb_*.joblib` — Trained model artifacts
- `data/processed/rul_*.csv` — Results, degradation rates, predictions

### Step 6 — Screen the cycles for anomalies (triage)

Flag out-of-norm cycles before anyone retrains or ships:

```bash
python src/anomaly.py
```

**Outputs:**
- `data/processed/anomaly_cycle_report.csv` — every cycle with all detector flags
- `data/processed/anomaly_events.csv` — only the 32 flagged cycles (5.0%)
- `data/processed/anomaly_summary.csv` — per-cell count/rate, including knee onset

### Step 7 — Benchmark inference latency

```bash
python src/benchmark_latency.py
```

**Output:** `data/processed/soh_latency_benchmark.csv`

Measured on this machine against the real artifact and the real
`assemble_features` path:

| Stage | ms |
|-------|----|
| `cold_model_load_ms` | 769 |
| `warm_predict_1_ms` | 2.27 |
| `warm_predict_soh_1_ms` (full API call) | 2.51 |
| `warm_predict_100_ms` | 3.29 |
| `warm_predict_1000_ms` | 15.8 |
| `end_to_end_cycle_ms` | 14.0 |
| `feature_extraction_ms` | 0.98 |

The cold load is paid once at service start and never per
request; the warm per-cycle cost is ~2.5 ms against a
~1-second cycle budget. `tests/test_latency.py` pins the shape
of this table but deliberately avoids a tight timing assertion,
because a machine under load makes those flaky.

### Step 8 — Prepare Dashboard Data

Load the processed CSV results into a SQLite database:

```bash
python dashboard/load_to_sqlite.py
```

### Step 9 — Launch the Grafana API

Start the Flask JSON API server which Grafana uses to fetch data:

```bash
nohup venv/bin/python dashboard/grafana_api.py > logs/grafana_api.log 2>&1 &
```

### Step 10 — Run tests

```bash
venv/bin/python -m pytest tests/ -q
```

The suite currently passes 133/133, in ~9 s. It also pins the two
failure-mode contracts (status flips to `failed` and the heartbeat
keeps republishing with a moving `age_s`), the data-quality failure
modes, the latency-budget shape, and the leakage guards (a
target-derived column injected into `FEATURES` must make the run
raise).

### Run everything in order

```bash
python run_pipeline.py                    # all nine stages
python run_pipeline.py --skip-train       # keep existing models
python run_pipeline.py --only train_soh   # one stage
```

Each stage runs as a subprocess in dependency order, and the
first non-zero exit stops the run with the failing stage named.
`train_soh` runs a nested leave-one-cell-out search and takes
tens of minutes, which is why `--skip-train` exists.

### Step 11 — Provision and start Grafana (no sudo needed)

Grafana is the only dashboard in this project. The provisioning
tree lives in the repo at `dashboard/provisioning/` so a fresh
checkout never requires copying files into `/etc/grafana`:

```bash
export BATTERY_PDM_ROOT="$(pwd)"
GF_PATHS_HOME=/usr/share/grafana \
GF_PATHS_DATA="$BATTERY_PDM_ROOT/.grafana-data" \
GF_PATHS_LOGS="$BATTERY_PDM_ROOT/logs" \
GF_PATHS_PLUGINS=/var/lib/grafana/plugins \
GF_PATHS_PROVISIONING="$BATTERY_PDM_ROOT/dashboard/provisioning" \
GF_SERVER_HTTP_PORT=3050 \
GF_SERVER_HTTP_ADDR=0.0.0.0 \
grafana-server --homepath /usr/share/grafana
```

This provisions the **Battery PDM** JSON datasource (pointing at
`http://localhost:8099`) and the **Battery PDM (Grafana)**
dashboard (10 panels: SOH/RUL degradations, predicted-vs-actual,
model summaries, anomaly residuals/fade-rate + events). It needs
the `simpod-json-datasource` plugin (v0.6.7+, already in
`/var/lib/grafana/plugins` here) and the Flask API from Step 7.

Open **[http://localhost:3050](http://localhost:3050)** in your browser to view the **Battery PDM (Grafana)** dashboard.

---

## Dashboard

Grafana is the only dashboard and monitoring surface. Streamlit is
gone: `dashboard/app.py` was deleted and `streamlit`/`plotly` were
removed from `requirements.txt`. The dashboard is built natively in
**Grafana** using the JSON API datasource plugin. It provides
visualizations of:

- **SOH Degradation Over Cycles**: Tracking capacity fade across battery cells.
- **Capacity Fade**: Absolute Amp-hour capacity loss.
- **Predicted vs Actuals**: Overlay of XGBoost predictions vs actual target values for both SOH and RUL.
- **Model Summaries**: Tables detailing Nested CV results and feature importance.
- **Anomaly: Residual (SOH – Predicted) with Robust Bounds** + **Flagged events**: points outside median ± 3.5 × MAD — a cycle that behaved unlike anything in the training set. This is the triage view: 32 flagged cycles (5.0%) out of 636.
- **Anomaly: Fade Rate and Knee Threshold**: a rising trailing fade rate crossing median + 3 × MAD means acceleration, not noise. Only B0006 crosses it, at row 228.
- **Anomaly Summary / Events**: per-cell counts (B0006: 17 flagged, 10.1%) and the event-level rows with directions.

---

## Dataset

**Source:** NASA Prognostics Center of Excellence — Battery Dataset

Four 18650 lithium-ion cells (B0005, B0006, B0007, B0018) were run through repeated charge/discharge/impedance cycles at room temperature until end of life.

| Cell | Cycles | Initial SOH | Final SOH | EOL Cycle (80%) |
|------|--------|-------------|-----------|-----------------|
| B0005 | 168 | 100% | 69.35% | 356 |
| B0006 | 168 | 100% | 56.69% | 202 |
| B0007 | 168 | 100% | 74.06% | 445 |
| B0018 | 132 | 100% | 72.29% | 185 |

### Extracted Features

The raw parser emits 22 columns per cycle.

- **Voltage:** mean, min, max, std, range, start, end, drop
- **Current:** mean, min, max, std
- **Temperature:** mean, min, max, std
- **Cycle:** discharge ordinal and ambient temperature
- **Derived:** discharge duration, resistance proxy

The **model input set is 19 columns**: the list above minus
`ambient_temperature` (constant at 24 °C across the four group-1
cells, dropped automatically by `drop_constant_features` on the
training split only). The target-derived columns that used to be
listed here (`capacity_change_ah`, `soh_change_pct`) are no longer
inputs at all — see *Leakage Prevention* above.

`resistance_proxy_ohm` is named a **proxy** deliberately. It is
voltage range over current range, not a measured internal
resistance, and the project does not treat it as one.

---

## Model Details

### Evaluation Protocol

All models use **leave-one-cell-out (LOCO)** cross-validation: one cell is held out entirely, the model trains on the remaining three, and predictions are made on a cell the model has never seen. This is the hardest available protocol because degradation curves differ between cells of the same chemistry.

### SOH Model

- **Algorithm:** XGBoost with `RandomizedSearchCV` (20 iterations, nested LOCO)
- **Best parameters:** `max_depth=2`, `learning_rate=0.01`, `n_estimators=800`, `subsample=0.7`, `reg_alpha=0.1`, `reg_lambda=2.0`
- **Pipeline:** `SimpleImputer(median)` → `XGBRegressor`

### RUL Model

- **Challenge:** With only 4 cells, the model has 3 training examples of full degradation trajectories. RUL is dominated by the held-out cell's own future behavior.
- **Finding:** No cycle-level feature model beats a constant predictor. The degradation rate analysis provides actionable per-cell fade rates instead.

---

## Technologies

| Category | Libraries |
|----------|-----------|
| Data | `pandas`, `numpy`, `scipy`, `sqlite3` |
| ML | `scikit-learn`, `xgboost` |
| Feature importance | XGBoost `feature_importances_` (gain-based) |
| Visualization | `matplotlib`, `seaborn` |
| API Backend | `flask`, `flask-cors` |
| Dashboard | `Grafana`, `simpod-json-datasource` |
| Serialization | `joblib` |

`shap` is pinned in `requirements.txt` but is not yet wired into
the pipeline; the rankings in `soh_feature_importance.csv` come
from XGBoost's built-in gain-based `feature_importances_`, not
from SHAP. SHAP would be the better instrument for the four-cell
regime, where a handful of cycles carry disproportionate gain.

---

## Known Limitations

These are limits of the data, not of the implementation, and no
amount of tuning removes them.

1. **Only four cells.** Every SOH number here comes from the four
   NASA group-1 cells B0005, B0006, B0007 and B0018. In
   leave-one-cell-out validation that is three training
   trajectories per fold. The model therefore generalises across
   *these* four ageing trajectories, not across lithium-ion cells
   in general.

2. **Cell-level generalisation is the open problem.** The report
   is a mean over four folds; the spread matters more than the
   mean. B0006 fails badly and for a diagnosable reason.

3. **B0006 cannot be predicted well by this feature set.** It
   degrades to 56.7% SOH, further than any cell the model ever
   trains on in its fold, so a tree ensemble averages toward its
   training floor and never predicts below ~69.9%. B0006's nested
   MAE is 7.32% against 1.79–2.59% for the other three. This is a
   data-coverage limit: no hyperparameter setting invents a
   degradation the training cells never exhibited. It is reported,
   not hidden, and B0006 is not deleted to improve the score.

4. **RUL does not beat a constant.** With three training
   trajectories, no cycle-level model outperforms the
   held-out cell's own mean (oracle MAE 77.78 vs best model
   79.27 cycles). The usable RUL-family output is the per-cell
   fade rate, not an absolute remaining-life number.

5. **Streaming versus offline cycle index.** Offline `cycle` is
   the raw array index; streaming `cycle` is the discharge
   ordinal. The two differ (B0005: 2..614 offline versus 1..167
   streamed). This is documented in `streaming/cycle_tracker.py`
   and pinned by tests, but it is a real train/serve skew, not a
   cosmetic one.

6. **One cycle-latency contract.** SOH is emitted once per
   completed discharge, not per sample, because several features
   are only defined at cycle end.

Accuracy is stated as measured. A leakage-free 3.4% mean MAE with
a documented 7.3% worst cell is the honest result for this dataset;
it is not a production accuracy claim.

---

## License

This project is for academic and research purposes.
