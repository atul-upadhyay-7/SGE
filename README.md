# SGE - Battery Predictive Maintenance (PDM)

An end-to-end Machine Learning pipeline and dashboard designed to predict the **State of Health (SOH)** and **Remaining Useful Life (RUL)** of lithium-ion batteries, as well as detect anomalous battery behavior. This project uses the popular **NASA Battery Dataset** to train predictive models using XGBoost.

## 🚀 Features

- **Automated Data Extraction:** Parse complex `.mat` files from the NASA dataset into structured formats.
- **Robust Feature Engineering:** Computes internal resistance proxies, capacity degradation, and state-of-health changes.
- **Predictive Modeling:** High-accuracy XGBoost models to predict SOH and RUL.
- **Anomaly Detection:** Identifies abnormal battery cycles and rapid degradation events.
- **Interactive Dashboard:** A Streamlit-based web application to visualize battery health, feature correlations, and model predictions in real time.

---

## 📁 Project Structure

```
├── BMS Dataset/           # Additional BMS data (if applicable)
├── data/
│   ├── raw/               # Place raw NASA Battery Dataset (.mat) files here
│   └── processed/         # Generated processed CSVs and predictions
├── models/                # Saved trained XGBoost models (.joblib)
├── notebooks/             # Jupyter notebooks for EDA and experimentation
├── src/                   # Core pipeline scripts
│   ├── load_data.py       # Parses .mat files and extracts discharge cycles
│   ├── preprocessing.py   # Cleans data and calculates target variables
│   ├── features.py        # Feature engineering logic
│   ├── train_soh.py       # Trains the State-of-Health prediction model
│   ├── train_rul.py       # Trains the Remaining Useful Life prediction model
│   └── anomaly.py         # Anomaly detection logic
├── dashboard/
│   └── app.py             # Streamlit interactive dashboard
├── requirements.txt       # Project dependencies
└── README.md              # Project documentation
```

---

## 🛠️ Setup & Installation

**1. Clone the repository**
```bash
git clone https://github.com/atul-upadhyay-7/SGE.git
cd SGE
```

**2. Create a virtual environment (Optional but recommended)**
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate
```

**3. Install dependencies**
```bash
pip install -r requirements.txt
```

**4. Download the Data**
Download the **NASA Battery Dataset** and extract the `.mat` files (e.g., `B0005.mat`, `B0006.mat`, `B0007.mat`, `B0018.mat`) into the `data/raw/` directory.

---

## ⚙️ Usage / Workflow

Follow these steps to run the pipeline from end to end:

### 1. Data Loading
Parse the raw `.mat` files to extract cycle-level battery data:
```bash
python src/load_data.py
```

### 2. Preprocessing & Feature Engineering
Clean the data and compute target labels (SOH/RUL) and features:
```bash
python src/preprocessing.py
```

### 3. Model Training
Train the XGBoost models and evaluate their performance (saves models to the `models/` directory):
```bash
python src/train_soh.py
python src/train_rul.py
```

### 4. Run the Dashboard
Launch the Streamlit interactive dashboard to view the results:
```bash
streamlit run dashboard/app.py
```
*The dashboard will be available in your browser at `http://localhost:8501`.*

---

## 📊 Technologies Used
- **Data Manipulation:** `pandas`, `numpy`
- **Machine Learning:** `scikit-learn`, `xgboost`, `scipy`
- **Model Explainability:** `shap`
- **Visualization:** `matplotlib`, `seaborn`, `streamlit`
- **Serialization:** `joblib`
