# Setup, architecture and operations

Everything needed to install, run, monitor and understand the pipeline. For the measured results see `docs/RESULTS.md`.

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
                                           │  (cleaned)       │
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


## Project Structure

```
├── data/
│   ├── raw/                           # NASA Battery .mat files (B0005–B0018)
│   └── processed/
│       ├── nasa_cycle_features_raw.csv    # Extracted cycle-level features
│       ├── nasa_ml_dataset.csv            # Pre-cleaning ML dataset
│       ├── nasa_ml_dataset_clean.csv      # Cleaned ML dataset (all stages read this)
│       ├── cell_audit.csv                 # Per-cell keep/exclude decision
│       ├── data_reference_sensitivity.csv # Keep/exclude under each reference rule
│       ├── soh_results.csv                # SOH model per-cell scores
│       ├── soh_baselines.csv              # Baseline model comparison
│       ├── soh_nested_results.csv         # Nested CV XGBoost results
│       ├── soh_feature_importance.csv     # Feature importance rankings
│       ├── soh_bias_report.csv            # Prediction bias analysis
│       ├── soh_null_baselines.csv         # Constant predictor baselines
│       ├── soh_predictions_*.csv          # Per-cell SOH predictions
│       ├── rul_results.csv                # RUL model scores
│       ├── rul_trajectory_results.csv     # Trajectory matching + reference spread
│       ├── rul_method_comparison.csv      # Trajectory vs fade-rate extrapolation
│       ├── rul_extrapolation_bands.csv    # Per-discharge extrapolation estimate
│       ├── rul_method_disagreement.csv    # Trajectory vs extrapolation gap per cell
│       ├── rul_band_calibration.csv       # Leave-one-cell-out band calibration
│       ├── rul_degradation_rates.csv      # Cell degradation characteristics
│       ├── rul_null_baselines.csv         # RUL constant baselines
│       ├── rul_predictions_*.csv          # Per-cell RUL predictions
│       ├── data_quality_report.csv/.json  # Data audit (cells, rows, fade, duplicates)
│       ├── random_load_rul_check.csv      # Do B0038-B0044 reach 80%? (count only)
│       └── soh_latency_benchmark.csv      # Measured inference latency
├── models/
│   ├── soh_xgb_*.joblib                  # SOH models (per-cell + global)
│   └── rul_xgb_*.joblib                  # RUL models (per-cell + global)
├── src/
│   ├── load_data.py                      # .mat file parser → raw features
│   ├── preprocessing.py                  # Data cleaning, target computation
│   ├── data_quality.py                   # Dataset audit, fails loudly on a bad table
│   ├── random_load.py                    # Random-load cells: do they reach 80%?
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

```bash
bash scripts/download_dataset.sh
```

The script downloads the
[NASA battery archive](https://www.nasa.gov/content/prognostics-center-of-excellence-data-set-repository),
extracts the `.mat` files and verifies them against
`scripts/checksums.sha256`. They land
in `BMS Dataset/Battery_DataSet/Battery_DataSet/`, which
`src/load_data.py` reads; the loader also checks `data/raw/` first, so
placing the `.mat` files (`B0005.mat`, `B0006.mat`, `B0007.mat`,
`B0018.mat`) there works as well.

### 5. Repository size

A fresh clone is large. The NASA `.mat` dataset (~199 MB) and early
commits of the model and database artifacts stay in `.git` history
even though `.gitignore` keeps them out of the working tree, so `.git`
is around 226 MB and `git clone` downloads all of it. Nothing in the
day-to-day workflow needs that history.

Shrinking it is a **separate, user-approved step**. This project does
not run it, `scripts/verify.sh` does not depend on it, and it rewrites
history — so it must be done on a throwaway clone, and the force-push
coordinated with anyone else who has the repository:

```bash
git clone https://github.com/atul-upadhyay-7/SGE.git shrink && cd shrink
python -m pip install git-filter-repo

# Remove the dataset blobs from every commit. They live under
# "BMS Dataset/" and data/raw/ and are re-downloaded by
# scripts/download_dataset.sh, so no working file is lost.
git filter-repo --invert-paths --path 'BMS Dataset' --path data/raw

# filter-repo drops the remote; re-add it and force-push the shortened
# history, then re-clone everywhere it is checked out.
git remote add origin https://github.com/atul-upadhyay-7/SGE.git
git push --force origin main

git count-objects -vH   # size-pack shows the smaller result
```

To also drop the historical model and database blobs (the current
`models/*.ubj`, `models/*.joblib` and `data/live_pack.db` are still
wanted), add `--path-glob 'models/*.joblib' --path-glob '*.mat'` and
restore the current files with `git checkout` from the pre-shrink
clone before committing. **Do not run `git filter-repo` as part of any
automated step in this repository.**


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

**Output:** `data/processed/nasa_ml_dataset.csv` (the pre-cleaning dataset)

### Step 2b — Clean the dataset and audit the cells

```bash
python src/data_cleaning.py
```

**Outputs:**
- `data/processed/nasa_ml_dataset_clean.csv` — the dataset every downstream stage reads
- `data/processed/cell_audit.csv` — per-cell keep/exclude decision and SOH range

Invalid discharges are dropped, SOH is recomputed against each
cell's reference capacity, and cells that cannot reach end of life
are labelled rather than silently capped. See the *Dataset* section.

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
- `data/processed/anomaly_events.csv` — only the flagged cycles, one row each
- `data/processed/anomaly_summary.csv` — per-cell count/rate, including knee onset

### Step 7 — Benchmark inference latency

```bash
python src/benchmark_latency.py
```

**Output:** `data/processed/soh_latency_benchmark.csv`

Measured on this machine against the real artifact and the real
`assemble_features` path; the per-stage numbers are written to
`data/processed/soh_latency_benchmark.csv` (they are machine
specific, so they are not pinned into this document).

The cold load is paid once at service start and never per
request; the warm per-cycle cost is well inside the ~1-second
cycle budget. `tests/test_latency.py` pins the shape of the
benchmark but deliberately avoids a tight timing assertion, because
a machine under load makes those flaky.

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

The suite passes locally; the exact count and command output are
recorded in `CHANGES.md`. It also pins the two
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
A second, **Battery PDM Prometheus** datasource is provisioned for
the alert rules; see [Alert rules](#alert-rules).

Open **[http://localhost:3050/d/battery-pdm-dash/battery-pdm-grafana](http://localhost:3050/d/battery-pdm-dash/battery-pdm-grafana)**
directly to skip the dashboard list. Credentials are
`admin` / `admin` unless overridden with
`GF_SECURITY_ADMIN_USER` / `GF_SECURITY_ADMIN_PASSWORD`.

To open it without signing in, launch Grafana with anonymous
access as above:

```bash
GF_AUTH_ANONYMOUS_ENABLED=true \
GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer \
```

#### Why the dashboard time range looks like 1970

The JSON datasource returns the cycle number as each series'
timestamp, so Grafana reads cycle 2 as epoch millisecond 2. The
dashboard time range is therefore pinned to the first second
after the epoch.

This matters: with Grafana's default "last 6 hours" range every
panel reports **"Data outside time range"** with an empty plot
area even though all the data is present. If you ever see that
banner, the range has been reset — press **Zoom to data** or pick
the pinned range in the time picker. The table panels are
unaffected, since they are not time-based.

## Live pack monitor (3S pack, MQTT)

`src/pack/` turns three per-cell SOH streams into a pack view: cell imbalance
(same-instant voltage spread), persistent weak cell, SOH mismatch, internal
resistance spike (DC resistance from the load step, against the cell's own
baseline), thermal levels and rate, an Isolation Forest second opinion, and
cycles to retirement at **80% capacity** (the brief's threshold).

```bash
docker compose --profile demo up --build   # broker, SOH services, bridge, API, Grafana, replay with faults
# open http://localhost:3050  ->  "Battery Pack Live (3S, MQTT)"  (admin / ${GRAFANA_ADMIN_PASSWORD:-admin})
```

Without Docker, run `mosquitto`, then `python src/pack/run_pack.py`,
`python src/pack/bridge.py`, `python dashboard/grafana_api.py`, start Grafana as
in Step 11, then `python src/pack/publisher.py --delay 1 --fault CELL2:resistance_spike:40`.
Use `--delay` of 0.7 s or more: faster replay dropped messages in testing.

![architecture](docs/architecture.png)

What is and is not verified, and the sources behind each threshold, are in
[`docs/RESEARCH.md`](docs/RESEARCH.md). The backend and ML interfaces — window
feature names, units, model file and the estimator payload — are in
[`docs/CONTRACT.md`](docs/CONTRACT.md), generated from `src/features.py` so the
two sides cannot drift. In short: the pipeline (MQTT, SOH
services, bridge, API, Grafana) was run end to end both directly and via
`docker compose --profile demo up --build` (all eight containers up, Grafana
serving both provisioned dashboards); the ESP32 firmware in `firmware/` is
**not tested on hardware**; all faults in demos are **synthetic**.

<!-- make_report:pack -->
| Fault | Injected | Rule detection | Isolation Forest | Rule median delay |
|---|---|---|---|---|
| cell_sag | 24 | 75.0% | 25.0% | 1.0 |
| resistance_spike | 24 | 100.0% | 100.0% | 1.0 |
| thermal | 24 | 100.0% | 70.8% | 8.0 |

Clean-pack false alarms at the resistance-spike and thermal thresholds: 0 and 0 over 564 discharges.
<!-- make_report:pack:end -->

Imbalance-type alerts are not meaningful in the offline table; see
`docs/RESEARCH.md`.

### Alert rules

Two Grafana rules are provisioned (`dashboard/provisioning/alerting/`
for the native run, `docker/grafana/provisioning/alerting/` for the
compose stack): SOH below the 80% retirement threshold, and peak
temperature above the **maximum the SOH model trained on**. That
temperature bound is generated from `nasa_ml_dataset_clean.csv` by
`scripts/make_alert_rules.py` — it is the data, not a hand-typed 55.
Each rule is a three-node pipeline, `query → reduce(last) →
threshold`, with the threshold as the condition, so Grafana reduces
a multi-point series to the single value it compares.

The alert query goes to a **Prometheus** datasource (uid
`battery-pdm-prom`), not to the JSON-datasource plugin. That plugin
declares `"alerting": false` and `"backend": false`, so Grafana's
alerting engine cannot query it (`plugin.unavailable`) and a rule on
it would never be evaluated. Instead `dashboard/alert_exporter.py`
serves the latest per-cell gauges (`battery_pdm_cell_soh_percent`,
`battery_pdm_cell_peak_temperature_celsius`, `battery_pdm_cell_resistance_ohm`)
from `data/live_pack.db`, Prometheus scrapes it, and Grafana reads
Prometheus. Both `noDataState` and `execErrState` are `Alerting`, so
a dead or erroring datasource is visible instead of looking healthy.

Grafana evaluates the rules itself. After replaying the NASA cells
with an injected thermal fault, Grafana's own alert API reports both
rules healthy and firing — one alert instance per failing cell:

```bash
curl -s -u admin:admin \
  'http://localhost:3050/api/prometheus/grafana/api/v1/rules'
```

`scripts/grafana_alerts.py` prints the same health and state per
rule, and `tests/test_alert_datasource.py` fails if any rule queries
a plugin whose `plugin.json` has `"alerting": false`.

The compose stack also provisions a **webhook contact point** to a
local mock receiver (`alert-sink`, `docker/webhook_receiver.py`) and a
notification policy that routes every firing alert to it; each
notification is appended to `logs/alerts_received.jsonl`. A firing rule
therefore ends in a delivery, not just a colour change, and
`tests/test_alert_delivery.py` fails if the contact point or the policy
that routes to it goes missing. `scripts/verify_alerts.sh` brings up the
demo stack, waits for Grafana to report a firing rule, then waits for
that payload to arrive at the receiver, and fails unless both happen.
(The lighter native `dashboard/` deployment provisions no contact point.)

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
- **Anomaly: Residual (SOH – Predicted) with Robust Bounds** + **Flagged events**: points outside median ± 3.5 × MAD — a cycle that behaved unlike anything in the training set. This is the triage view; per-cell counts and rates are in `data/processed/anomaly_summary.csv`.
- **Anomaly: Fade Rate and Knee Threshold**: a rising trailing fade rate crossing median + 3 × MAD means acceleration, not noise. The first knee cycle per cell is in `anomaly_summary.csv`.
- **Anomaly Summary / Events**: per-cell counts and the event-level rows with directions, served from the `anomaly_summary` and `anomaly_events` tables.

The Battery PDM dashboard, captured from the running stack (`docker compose
--profile demo up --build`, then `http://localhost:3050`):

![Battery PDM dashboard](docs/battery_pdm_dashboard.png)

The live 3S pack monitor with the demo replay and injected faults:

![Battery pack live](docs/battery_pack_live.png)

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

## Reproducible training and current limits

Use Python 3.12 with `requirements.txt`. The Docker base matches this version.

```bash
python train.py --check
python train.py --quick
python train.py
python run_pipeline.py --skip-train
```

The driver backs up models/results and writes per-stage logs and quality-gate reports. `--quick` is a smoke test, not the headline 20-iteration result. `--only` is diagnostic and never claims a full training pass. The quality gates are smoke checks on four NASA cells, not client acceptance or hardware validation. Run `python -m pytest -q` for the current test count, and `bash scripts/verify.sh` to re-verify a clean clone end to end.

What is still unverified: real pack data, variable-load validation and ESP32 hardware. The retirement threshold stays at 80% pending the team decision; the older tracker plan says 70%. Other pack rules fire often on this dataset, so the overall false-alarm count is not zero even though the resistance-spike and thermal thresholds stayed clean. Alerts are evaluated and fire in Grafana (see *Alert rules*), and the firing alert is delivered to a provisioned local webhook contact point (`alert-sink` in the compose stack), so the notification path is exercised end to end; no external email/pager endpoint is configured.

Repository size: `.git` is about 226 MB because the NASA dataset and the trained models were committed early. They are ignored now (`*.mat`, `BMS Dataset/`, `data/raw/`) but the blobs stay in history, and the history is not rewritten; see the size note in `CHANGES.md`. A fresh clone downloads all of it.
