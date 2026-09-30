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
| **Mean MAE** | **3.23%** |
| Mean RMSE | 3.76% |
| Mean R² | 0.85 |

**Per-cell breakdown:**

| Cell | MAE (%) | RMSE (%) | R² |
|------|---------|----------|----|
| B0005 | 2.53 | 3.37 | 0.89 |
| B0006 | 6.76 | 7.16 | 0.66 |
| B0007 | 1.80 | 2.23 | 0.93 |
| B0018 | 1.82 | 2.28 | 0.92 |

Top 3 predictive features: `voltage_mean`, `temperature_std`, `current_std`

### RUL Prediction (Remaining Useful Life)

| Metric | Value |
|--------|-------|
| Target | Cycles remaining until 80% SOH |
| Best Model | Linear (with cycle feature dropped) |
| **Mean MAE** | **85.97 cycles** |

> **Note:** With only four cells, RUL prediction is fundamentally limited by data coverage. The model cannot outperform a constant predictor (oracle cell mean MAE: 77.78 cycles) because each cell's degradation trajectory is unique and the training set provides only three examples of how a cell ages.

### Battery Degradation Characteristics

| Cell | EOL Cycle | Fade Rate (%/cycle) | Early Fade (%/cycle) |
|------|-----------|---------------------|----------------------|
| B0005 | 356 | 0.0567 | 0.0184 |
| B0006 | 202 | 0.0875 | 0.0887 |
| B0007 | 445 | 0.0499 | 0.0216 |
| B0018 | 185 | 0.1001 | 0.1297 |

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
src/streaming/soh_service.py     latched SOH, 1.7 s one-time load, ~2.5 ms per prediction
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
│       └── rul_predictions_*.csv          # Per-cell RUL predictions
├── models/
│   ├── soh_xgb_*.joblib                  # SOH models (per-cell + global)
│   └── rul_xgb_*.joblib                  # RUL models (per-cell + global)
├── src/
│   ├── load_data.py                      # .mat file parser → raw features
│   ├── preprocessing.py                  # Data cleaning, target computation
│   ├── features.py                       # Feature list and extraction
│   ├── evaluation.py                     # Shared scoring and LOCO protocol
│   ├── train_soh.py                      # SOH model training pipeline
│   ├── train_rul.py                      # RUL model training pipeline
│   ├── anomaly.py                        # Anomaly detection: LOCO residual + capacity event + fade-rate knee
├── dashboard/
│   ├── load_to_sqlite.py                 # Loads processed CSVs to SQLite
│   ├── grafana_api.py                    # Flask API for Grafana JSON datasource
│   └── provisioning/                     # Ran with Grafana ≥ 13.x as GF_PATHS_PROVISIONING; see "Run the whole thing" below.
├── notebooks/                            # Jupyter notebooks for EDA
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

### Step 3 — Train SOH Model

Train XGBoost with nested leave-one-cell-out hyperparameter search:

```bash
python src/train_soh.py
```

**Outputs:**
- `models/soh_xgb_*.joblib` — Trained model artifacts
- `data/processed/soh_*.csv` — Results, baselines, predictions, feature importance

### Step 4 — Train RUL Model

Evaluate multiple models for remaining useful life prediction:

```bash
python src/train_rul.py
```

**Outputs:**
- `models/rul_xgb_*.joblib` — Trained model artifacts
- `data/processed/rul_*.csv` — Results, degradation rates, predictions

### Step 5 — Screen the cycles for anomalies (triage)

Flag out-of-norm cycles before anyone retrains or ships:

```bash
python src/anomaly.py
```

**Outputs:**
- `data/processed/anomaly_cycle_report.csv` — every cycle with all detector flags
- `data/processed/anomaly_events.csv` — only the 33 flagged cycles (5.2%)
- `data/processed/anomaly_summary.csv` — per-cell count/rate, including knee onset

### Step 6 — Prepare Dashboard Data

Load the processed CSV results into a SQLite database:

```bash
python dashboard/load_to_sqlite.py
```

### Step 7 — Launch the Grafana API

Start the Flask JSON API server which Grafana uses to fetch data:

```bash
nohup venv/bin/python dashboard/grafana_api.py > logs/grafana_api.log 2>&1 &
```

### Step 8 — Run tests

```bash
venv/bin/python -m pytest tests/ -q
```

The suite currently passes 82/82, in ~9 s. It also pins the two
failure-mode contracts (status flips to `failed` and the heartbeat
keeps republishing with a moving `age_s`).

### Step 9 — Provision and start Grafana (no sudo needed)

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
- **Anomaly: Residual (SOH – Predicted) with Robust Bounds** + **Flagged events**: points outside median ± 3.5 × MAD — a cycle that behaved unlike anything in the training set. This is the triage view: 33 flagged cycles (5.2%) out of 636.
- **Anomaly: Fade Rate and Knee Threshold**: a rising trailing fade rate crossing median + 3 × MAD means acceleration, not noise. Only B0006 crosses it, at row 228.
- **Anomaly Summary / Events**: per-cell counts (B0006: 18 flagged, 10.7%) and the event-level rows with directions.

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

### Extracted Features (22 per cycle)

- **Voltage:** mean, min, max, std, range, start, end, drop
- **Current:** mean, min, max, std
- **Temperature:** mean, min, max, std
- **Derived:** discharge duration, resistance proxy, capacity, SOH, capacity change, SOH change

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
| Explainability | `shap` |
| Visualization | `matplotlib`, `seaborn` |
| API Backend | `flask`, `flask-cors` |
| Dashboard | `Grafana`, `simpod-json-datasource` |
| Serialization | `joblib` |

---

## License

This project is for academic and research purposes.
