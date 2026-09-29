# SGE — Battery Predictive Maintenance System

An end-to-end machine-learning pipeline and interactive monitoring dashboard for predicting the **State of Health (SOH)** and **Remaining Useful Life (RUL)** of lithium-ion batteries. Built on the NASA Battery Dataset, the system trains per-cell and global XGBoost models using a rigorous leave-one-cell-out cross-validation protocol and serves results through a Grafana-themed Streamlit dashboard.

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
                                          ┌────────▼────────┐
                                          │  dashboard/app.py│
                                          │  (Streamlit)     │
                                          └─────────────────┘
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
│   └── anomaly.py                        # Anomaly detection (placeholder)
├── dashboard/
│   └── app.py                            # Grafana-themed Streamlit dashboard
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
pip install plotly           # Required for the dashboard charts
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

### Step 5 — Launch the Dashboard

```bash
streamlit run dashboard/app.py
```

Open [http://localhost:8501](http://localhost:8501) in your browser.

### One-liner (full pipeline)

```bash
python src/load_data.py && python src/preprocessing.py && python src/train_soh.py && python src/train_rul.py && streamlit run dashboard/app.py
```

---

## Dashboard

The interactive dashboard is built with Streamlit using a **Grafana-inspired dark theme** and Plotly charts. It has six pages:

| Page | Description |
|------|-------------|
| **Overview** | KPI metrics, SOH/capacity degradation curves, voltage & temperature trends, model registry |
| **SOH Analysis** | Actual vs predicted SOH with residual overlays, model comparison, bias analysis, feature importance |
| **RUL Analysis** | RUL predictions per cell, model score comparison, null baselines, degradation rate charts |
| **Feature Explorer** | Interactive time series for any feature, correlation heatmap, per-cell distributions |
| **Data Inspector** | Browse any processed CSV — schema, statistics, null analysis, data preview |
| **Pipeline Logs** | Structured execution logs with level/source filtering, source code viewer |

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
| Data | `pandas`, `numpy`, `scipy` |
| ML | `scikit-learn`, `xgboost` |
| Explainability | `shap` |
| Visualization | `matplotlib`, `seaborn`, `plotly` |
| Dashboard | `streamlit` |
| Serialization | `joblib` |

---

## License

This project is for academic and research purposes.
