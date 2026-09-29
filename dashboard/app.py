"""
Battery Predictive Maintenance Dashboard
-----------------------------------------
Grafana-themed Streamlit dashboard for SOH/RUL model results,
battery health monitoring, feature analysis and pipeline logs.
"""

import os
import glob
import datetime

import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

# ── paths ──────────────────────────────────────────────────────
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data", "processed")
MODELS = os.path.join(BASE, "models")
SRC = os.path.join(BASE, "src")

# ── grafana palette ────────────────────────────────────────────
GRAFANA_BG = "#0b0c0e"
GRAFANA_PANEL = "#141619"
GRAFANA_BORDER = "#1f2229"
GRAFANA_TEXT = "#d8d9da"
GRAFANA_TEXT_DIM = "#8e8e8e"
GRAFANA_GREEN = "#73bf69"
GRAFANA_YELLOW = "#fade2a"
GRAFANA_ORANGE = "#ff9830"
GRAFANA_RED = "#f2495c"
GRAFANA_BLUE = "#5794f2"
GRAFANA_PURPLE = "#b877d9"
GRAFANA_TEAL = "#36a2eb"
GRAFANA_CYAN = "#8ab8ff"

CELL_COLORS = {
    "B0005": GRAFANA_GREEN,
    "B0006": GRAFANA_ORANGE,
    "B0007": GRAFANA_BLUE,
    "B0018": GRAFANA_PURPLE,
}

GRAFANA_SERIES = [
    GRAFANA_GREEN, GRAFANA_ORANGE, GRAFANA_BLUE,
    GRAFANA_PURPLE, GRAFANA_TEAL, GRAFANA_YELLOW,
    GRAFANA_RED, GRAFANA_CYAN,
]

# ── page config ────────────────────────────────────────────────
st.set_page_config(
    page_title="Battery PDM · Dashboard",
    page_icon="🔋",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── inject grafana CSS ─────────────────────────────────────────
st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

    /* root overrides */
    :root {{
        --bg:        {GRAFANA_BG};
        --panel:     {GRAFANA_PANEL};
        --border:    {GRAFANA_BORDER};
        --text:      {GRAFANA_TEXT};
        --text-dim:  {GRAFANA_TEXT_DIM};
        --green:     {GRAFANA_GREEN};
        --yellow:    {GRAFANA_YELLOW};
        --orange:    {GRAFANA_ORANGE};
        --red:       {GRAFANA_RED};
        --blue:      {GRAFANA_BLUE};
    }}

    html, body, [data-testid="stAppViewContainer"],
    [data-testid="stHeader"], [data-testid="stSidebar"],
    [data-testid="stSidebar"] > div {{
        background-color: var(--bg) !important;
        color: var(--text) !important;
        font-family: 'Inter', -apple-system, sans-serif !important;
    }}

    [data-testid="stSidebar"] {{
        background-color: #111217 !important;
        border-right: 1px solid var(--border) !important;
    }}

    /* metric cards */
    [data-testid="stMetric"] {{
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 4px;
        padding: 14px 18px;
    }}
    [data-testid="stMetricLabel"] {{
        color: var(--text-dim) !important;
        font-size: 11px !important;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }}
    [data-testid="stMetricValue"] {{
        color: var(--text) !important;
        font-weight: 600 !important;
    }}
    [data-testid="stMetricDelta"] svg {{
        display: none;
    }}

    /* tabs */
    [data-testid="stTabs"] button {{
        color: var(--text-dim) !important;
        background: transparent !important;
        border: none !important;
        border-bottom: 2px solid transparent !important;
        font-size: 13px !important;
        font-weight: 500;
        padding: 8px 16px !important;
    }}
    [data-testid="stTabs"] button[aria-selected="true"] {{
        color: var(--text) !important;
        border-bottom: 2px solid var(--blue) !important;
    }}

    /* dataframes */
    [data-testid="stDataFrame"] {{
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 4px;
    }}

    /* expanders */
    [data-testid="stExpander"] {{
        background: var(--panel) !important;
        border: 1px solid var(--border) !important;
        border-radius: 4px !important;
    }}

    /* markdown headings */
    h1, h2, h3, h4, h5, h6 {{
        color: var(--text) !important;
        font-family: 'Inter', sans-serif !important;
    }}
    h1 {{ font-weight: 600 !important; font-size: 24px !important; }}
    h2 {{ font-weight: 600 !important; font-size: 18px !important; }}
    h3 {{ font-weight: 500 !important; font-size: 15px !important; }}

    /* sidebar radio */
    [data-testid="stSidebar"] label {{
        color: var(--text) !important;
    }}

    /* code blocks */
    code {{
        background: #1a1b1f !important;
        color: {GRAFANA_GREEN} !important;
        border: 1px solid var(--border) !important;
        border-radius: 3px;
        padding: 1px 5px;
        font-size: 12px;
    }}

    /* selectbox / multiselect */
    [data-testid="stSelectbox"] > div,
    [data-testid="stMultiSelect"] > div {{
        background: var(--panel) !important;
        border-color: var(--border) !important;
    }}

    /* divider */
    hr {{ border-color: var(--border) !important; }}

    /* scrollbar */
    ::-webkit-scrollbar {{ width: 6px; }}
    ::-webkit-scrollbar-track {{ background: var(--bg); }}
    ::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}

    /* panel class */
    .gf-panel {{
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 4px;
        padding: 16px;
        margin-bottom: 8px;
    }}
    .gf-panel-title {{
        color: var(--text-dim);
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.6px;
        margin-bottom: 10px;
        font-weight: 500;
    }}
    .gf-stat {{
        font-size: 32px;
        font-weight: 700;
        line-height: 1.1;
    }}
    .gf-stat-label {{
        color: var(--text-dim);
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.6px;
        margin-top: 4px;
    }}
    .gf-log {{
        background: #0d0e10;
        border: 1px solid var(--border);
        border-radius: 4px;
        padding: 12px 16px;
        font-family: 'JetBrains Mono', 'Fira Code', monospace;
        font-size: 12px;
        color: var(--text);
        line-height: 1.6;
        max-height: 420px;
        overflow-y: auto;
        white-space: pre-wrap;
        word-break: break-all;
    }}
    .gf-log .ts {{ color: {GRAFANA_TEXT_DIM}; }}
    .gf-log .info {{ color: {GRAFANA_GREEN}; }}
    .gf-log .warn {{ color: {GRAFANA_YELLOW}; }}
    .gf-log .err {{ color: {GRAFANA_RED}; }}
    .gf-badge {{
        display: inline-block;
        padding: 2px 8px;
        border-radius: 3px;
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
    }}
    .gf-badge-green {{ background: rgba(115,191,105,0.15); color: {GRAFANA_GREEN}; }}
    .gf-badge-red {{ background: rgba(242,73,92,0.15); color: {GRAFANA_RED}; }}
    .gf-badge-yellow {{ background: rgba(250,222,42,0.15); color: {GRAFANA_YELLOW}; }}
    .gf-badge-blue {{ background: rgba(87,148,242,0.15); color: {GRAFANA_BLUE}; }}

    /* Top bar */
    .gf-topbar {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 8px 0;
        border-bottom: 1px solid var(--border);
        margin-bottom: 20px;
    }}
    .gf-topbar-left {{
        display: flex;
        align-items: center;
        gap: 12px;
    }}
    .gf-topbar-title {{
        font-size: 20px;
        font-weight: 600;
        color: var(--text);
    }}
    .gf-topbar-right {{
        display: flex;
        align-items: center;
        gap: 14px;
        color: var(--text-dim);
        font-size: 12px;
    }}
</style>
""", unsafe_allow_html=True)


# ── helper: plotly layout ──────────────────────────────────────
def grafana_layout(fig, title="", height=380, showlegend=True):
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=GRAFANA_PANEL,
        plot_bgcolor=GRAFANA_PANEL,
        font=dict(family="Inter, sans-serif", color=GRAFANA_TEXT, size=12),
        title=dict(
            text=title,
            font=dict(size=13, color=GRAFANA_TEXT_DIM),
            x=0.01, y=0.97,
        ),
        height=height,
        margin=dict(l=50, r=20, t=40, b=40),
        showlegend=showlegend,
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(size=11, color=GRAFANA_TEXT_DIM),
            orientation="h", y=-0.18,
        ),
        xaxis=dict(
            gridcolor="#1f2229", zerolinecolor="#1f2229",
            tickfont=dict(size=10, color=GRAFANA_TEXT_DIM),
        ),
        yaxis=dict(
            gridcolor="#1f2229", zerolinecolor="#1f2229",
            tickfont=dict(size=10, color=GRAFANA_TEXT_DIM),
        ),
    )
    return fig


# ── data loaders (cached) ─────────────────────────────────────
@st.cache_data
def load_csv(name):
    p = os.path.join(DATA, name)
    if os.path.exists(p):
        return pd.read_csv(p)
    return None


@st.cache_data
def load_all_soh_predictions():
    frames = []
    for cell in ["B0005", "B0006", "B0007", "B0018"]:
        f = os.path.join(DATA, f"soh_predictions_{cell}.csv")
        if os.path.exists(f):
            frames.append(pd.read_csv(f))
    if frames:
        return pd.concat(frames, ignore_index=True)
    return None


@st.cache_data
def load_all_rul_predictions():
    frames = []
    for cell in ["B0005", "B0006", "B0007", "B0018"]:
        f = os.path.join(DATA, f"rul_predictions_{cell}.csv")
        if os.path.exists(f):
            frames.append(pd.read_csv(f))
    if frames:
        return pd.concat(frames, ignore_index=True)
    return None


@st.cache_data
def get_model_files():
    files = []
    for f in sorted(glob.glob(os.path.join(MODELS, "*.joblib"))):
        stat = os.stat(f)
        files.append({
            "file": os.path.basename(f),
            "size_kb": round(stat.st_size / 1024, 1),
            "modified": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return pd.DataFrame(files)


@st.cache_data
def get_source_files():
    files = []
    for f in sorted(glob.glob(os.path.join(SRC, "*.py"))):
        stat = os.stat(f)
        with open(f) as fh:
            lines = len(fh.readlines())
        files.append({
            "file": os.path.basename(f),
            "lines": lines,
            "size_kb": round(stat.st_size / 1024, 1),
            "modified": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return pd.DataFrame(files)


# ── load data ──────────────────────────────────────────────────
df_raw = load_csv("nasa_cycle_features_raw.csv")
df_ml = load_csv("nasa_ml_dataset.csv")
df_soh_results = load_csv("soh_results.csv")
df_soh_baselines = load_csv("soh_baselines.csv")
df_soh_nested = load_csv("soh_nested_results.csv")
df_soh_feat_imp = load_csv("soh_feature_importance.csv")
df_soh_bias = load_csv("soh_bias_report.csv")
df_soh_null = load_csv("soh_null_baselines.csv")
df_rul_results = load_csv("rul_results.csv")
df_rul_degrad = load_csv("rul_degradation_rates.csv")
df_rul_null = load_csv("rul_null_baselines.csv")
df_soh_pred = load_all_soh_predictions()
df_rul_pred = load_all_rul_predictions()
df_models = get_model_files()
df_sources = get_source_files()

cells = ["B0005", "B0006", "B0007", "B0018"]

# ── sidebar ────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"""
    <div style="padding:8px 0 16px 0;">
        <div style="display:flex;align-items:center;gap:10px;">
            <span style="font-size:24px;">🔋</span>
            <div>
                <div style="font-size:16px;font-weight:600;color:{GRAFANA_TEXT};">Battery PDM</div>
                <div style="font-size:11px;color:{GRAFANA_TEXT_DIM};">Predictive Maintenance</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f'<hr style="margin:0 0 12px 0;border-color:{GRAFANA_BORDER};">', unsafe_allow_html=True)

    page = st.radio(
        "Navigation",
        ["Overview", "SOH Analysis", "RUL Analysis",
         "Feature Explorer", "Data Inspector", "Pipeline Logs"],
        label_visibility="collapsed",
    )

    st.markdown(f'<hr style="margin:12px 0;border-color:{GRAFANA_BORDER};">', unsafe_allow_html=True)

    st.markdown(f"""
    <div style="font-size:11px;color:{GRAFANA_TEXT_DIM};padding:4px 0;">
        <div style="margin-bottom:6px;">
            <span style="color:{GRAFANA_TEXT};">Dataset</span> · NASA Battery
        </div>
        <div style="margin-bottom:6px;">
            <span style="color:{GRAFANA_TEXT};">Cells</span> · B0005, B0006, B0007, B0018
        </div>
        <div style="margin-bottom:6px;">
            <span style="color:{GRAFANA_TEXT};">Models</span> · {len(df_models)} trained
        </div>
        <div>
            <span style="color:{GRAFANA_TEXT};">Source</span> · {df_sources['lines'].sum()} lines
        </div>
    </div>
    """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════
#  PAGE: OVERVIEW
# ═══════════════════════════════════════════════════════════════
if page == "Overview":

    # top bar
    st.markdown(f"""
    <div class="gf-topbar">
        <div class="gf-topbar-left">
            <span class="gf-topbar-title">System Overview</span>
            <span class="gf-badge gf-badge-green">HEALTHY</span>
        </div>
        <div class="gf-topbar-right">
            <span>Last trained · {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # KPI row
    total_cycles = len(df_ml) if df_ml is not None else 0
    soh_mae = df_soh_nested["MAE"].mean() if df_soh_nested is not None else 0
    soh_r2 = df_soh_nested["R2"].mean() if df_soh_nested is not None else 0
    n_models = len(df_models) if df_models is not None else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Cycles", f"{total_cycles:,}")
    c2.metric("Battery Cells", len(cells))
    c3.metric("SOH MAE", f"{soh_mae:.2f}%")
    c4.metric("SOH R²", f"{soh_r2:.4f}")
    c5.metric("Trained Models", n_models)

    st.markdown("", unsafe_allow_html=True)

    # row: SOH degradation + capacity
    col_left, col_right = st.columns(2)

    with col_left:
        if df_ml is not None:
            fig = go.Figure()
            for cell in cells:
                sub = df_ml[df_ml["cell_id"] == cell].sort_values("cycle")
                fig.add_trace(go.Scatter(
                    x=sub["cycle"], y=sub["soh"],
                    mode="lines",
                    name=cell,
                    line=dict(color=CELL_COLORS[cell], width=2),
                    hovertemplate=f"{cell}<br>Cycle: %{{x}}<br>SOH: %{{y:.2f}}%<extra></extra>",
                ))
            fig.add_hline(y=80, line_dash="dot", line_color=GRAFANA_RED,
                          annotation_text="EOL threshold (80%)",
                          annotation_font_color=GRAFANA_RED,
                          annotation_font_size=10)
            grafana_layout(fig, "SOH Degradation Over Cycles")
            st.plotly_chart(fig, use_container_width=True)

    with col_right:
        if df_ml is not None:
            fig = go.Figure()
            for cell in cells:
                sub = df_ml[df_ml["cell_id"] == cell].sort_values("cycle")
                fig.add_trace(go.Scatter(
                    x=sub["cycle"], y=sub["capacity_ah"],
                    mode="lines",
                    name=cell,
                    line=dict(color=CELL_COLORS[cell], width=2),
                    hovertemplate=f"{cell}<br>Cycle: %{{x}}<br>Capacity: %{{y:.4f}} Ah<extra></extra>",
                ))
            grafana_layout(fig, "Capacity Fade (Ah)")
            st.plotly_chart(fig, use_container_width=True)

    # row: voltage + temperature
    col_left, col_right = st.columns(2)

    with col_left:
        if df_ml is not None:
            fig = go.Figure()
            for cell in cells:
                sub = df_ml[df_ml["cell_id"] == cell].sort_values("cycle")
                fig.add_trace(go.Scatter(
                    x=sub["cycle"], y=sub["voltage_mean"],
                    mode="lines",
                    name=cell,
                    line=dict(color=CELL_COLORS[cell], width=1.5),
                ))
            grafana_layout(fig, "Mean Discharge Voltage")
            st.plotly_chart(fig, use_container_width=True)

    with col_right:
        if df_ml is not None:
            fig = go.Figure()
            for cell in cells:
                sub = df_ml[df_ml["cell_id"] == cell].sort_values("cycle")
                fig.add_trace(go.Scatter(
                    x=sub["cycle"], y=sub["temperature_mean"],
                    mode="lines",
                    name=cell,
                    line=dict(color=CELL_COLORS[cell], width=1.5),
                ))
            grafana_layout(fig, "Mean Discharge Temperature (°C)")
            st.plotly_chart(fig, use_container_width=True)

    # model registry table
    st.markdown("### Model Registry")
    if df_models is not None and len(df_models) > 0:
        st.dataframe(
            df_models.rename(columns={"file": "Model File", "size_kb": "Size (KB)", "modified": "Last Modified"}),
            hide_index=True, use_container_width=True,
        )

    # source files
    st.markdown("### Source Files")
    if df_sources is not None and len(df_sources) > 0:
        st.dataframe(
            df_sources.rename(columns={"file": "File", "lines": "Lines", "size_kb": "Size (KB)", "modified": "Modified"}),
            hide_index=True, use_container_width=True,
        )


# ═══════════════════════════════════════════════════════════════
#  PAGE: SOH ANALYSIS
# ═══════════════════════════════════════════════════════════════
elif page == "SOH Analysis":

    st.markdown(f"""
    <div class="gf-topbar">
        <div class="gf-topbar-left">
            <span class="gf-topbar-title">State of Health · Model Analysis</span>
            <span class="gf-badge gf-badge-green">XGBoost</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    tab1, tab2, tab3, tab4 = st.tabs([
        "Predictions", "Model Comparison", "Bias Report", "Feature Importance"
    ])

    # ── tab: predictions ───────────────────────────────────────
    with tab1:
        if df_soh_pred is not None:
            sel = st.multiselect(
                "Select cells", cells, default=cells, key="soh_cells"
            )
            for cell in sel:
                sub = df_soh_pred[df_soh_pred["cell_id"] == cell].sort_values("cycle")
                if "predicted_soh" not in sub.columns:
                    continue
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=sub["cycle"], y=sub["soh"],
                    mode="lines", name="Actual SOH",
                    line=dict(color=GRAFANA_TEXT, width=2),
                ))
                fig.add_trace(go.Scatter(
                    x=sub["cycle"], y=sub["predicted_soh"],
                    mode="lines", name="Predicted SOH",
                    line=dict(color=CELL_COLORS[cell], width=2, dash="dash"),
                ))
                # residual band
                residual = sub["soh"] - sub["predicted_soh"]
                fig.add_trace(go.Bar(
                    x=sub["cycle"], y=residual,
                    name="Residual",
                    marker_color=[GRAFANA_GREEN if abs(r) < 3 else GRAFANA_YELLOW if abs(r) < 6 else GRAFANA_RED for r in residual],
                    opacity=0.3, yaxis="y2",
                ))
                fig.update_layout(
                    yaxis2=dict(
                        overlaying="y", side="right",
                        showgrid=False,
                        title="Residual (%)",
                        range=[-15, 15],
                        tickfont=dict(size=10, color=GRAFANA_TEXT_DIM),
                    ),
                )
                grafana_layout(fig, f"{cell} · SOH Actual vs Predicted", height=340)
                st.plotly_chart(fig, use_container_width=True)

    # ── tab: model comparison ──────────────────────────────────
    with tab2:
        col_a, col_b = st.columns(2)

        with col_a:
            st.markdown("#### Baseline Ladder (Leave-One-Cell-Out)")
            if df_soh_baselines is not None:
                models_agg = df_soh_baselines.groupby("model").agg(
                    MAE=("MAE", "mean"), RMSE=("RMSE", "mean"), R2=("R2", "mean")
                ).sort_values("MAE")

                fig = go.Figure()
                colors = [GRAFANA_GREEN if i == 0 else GRAFANA_SERIES[i % len(GRAFANA_SERIES)] for i in range(len(models_agg))]
                fig.add_trace(go.Bar(
                    x=models_agg.index, y=models_agg["MAE"],
                    marker_color=colors,
                    text=[f"{v:.2f}" for v in models_agg["MAE"]],
                    textposition="outside",
                    textfont=dict(color=GRAFANA_TEXT, size=11),
                ))
                grafana_layout(fig, "Mean MAE by Model", showlegend=False)
                fig.update_layout(yaxis_title="MAE (%)")
                st.plotly_chart(fig, use_container_width=True)

        with col_b:
            st.markdown("#### Nested CV vs Baselines")
            if df_soh_nested is not None and df_soh_null is not None:
                nested_mae = df_soh_nested["MAE"].mean()
                null_agg = df_soh_null.groupby("baseline")["MAE"].mean().sort_values()

                all_labels = ["XGBoost (nested)"] + list(null_agg.index)
                all_vals = [nested_mae] + list(null_agg.values)
                all_colors = [GRAFANA_GREEN] + [GRAFANA_TEXT_DIM] * len(null_agg)

                fig = go.Figure(go.Bar(
                    x=all_labels, y=all_vals,
                    marker_color=all_colors,
                    text=[f"{v:.2f}" for v in all_vals],
                    textposition="outside",
                    textfont=dict(color=GRAFANA_TEXT, size=11),
                ))
                grafana_layout(fig, "Model vs Constant Baselines", showlegend=False)
                fig.update_layout(yaxis_title="MAE (%)")
                st.plotly_chart(fig, use_container_width=True)

        # per-cell results table
        st.markdown("#### Per-Cell Results (Nested XGBoost)")
        if df_soh_nested is not None:
            styled = df_soh_nested.copy()
            styled["MAE"] = styled["MAE"].round(4)
            styled["RMSE"] = styled["RMSE"].round(4)
            styled["R2"] = styled["R2"].round(4)
            st.dataframe(styled, hide_index=True, use_container_width=True)

    # ── tab: bias report ───────────────────────────────────────
    with tab3:
        if df_soh_bias is not None:
            st.markdown("#### Prediction Bias Analysis")
            st.markdown(f"""
            <div style="color:{GRAFANA_TEXT_DIM};font-size:13px;margin-bottom:16px;">
                Bias reveals systematic over- or under-prediction per cell.
                Positive bias means the model over-predicts SOH (optimistic),
                negative means it under-predicts (conservative).
            </div>
            """, unsafe_allow_html=True)

            fig = go.Figure()
            for _, row in df_soh_bias.iterrows():
                cell = row["test_cell"]
                fig.add_trace(go.Bar(
                    x=[cell], y=[row["bias"]],
                    marker_color=GRAFANA_GREEN if row["bias"] < 0 else GRAFANA_RED,
                    name=cell,
                    text=f"{row['bias']:.2f}",
                    textposition="outside",
                    textfont=dict(color=GRAFANA_TEXT, size=11),
                    showlegend=False,
                ))
            fig.add_hline(y=0, line_color=GRAFANA_TEXT_DIM, line_width=1)
            grafana_layout(fig, "Mean Prediction Bias per Cell", height=320, showlegend=False)
            fig.update_layout(yaxis_title="Bias (%)")
            st.plotly_chart(fig, use_container_width=True)

            # range comparison
            col_l, col_r = st.columns(2)
            with col_l:
                fig = go.Figure()
                for _, row in df_soh_bias.iterrows():
                    cell = row["test_cell"]
                    fig.add_trace(go.Scatter(
                        x=[row["actual_min"], row["actual_max"]],
                        y=[cell, cell],
                        mode="lines+markers",
                        name=f"{cell} actual",
                        line=dict(color=GRAFANA_TEXT, width=3),
                        marker=dict(size=8),
                    ))
                    fig.add_trace(go.Scatter(
                        x=[row["predicted_min"], row["predicted_max"]],
                        y=[cell, cell],
                        mode="lines+markers",
                        name=f"{cell} predicted",
                        line=dict(color=CELL_COLORS[cell], width=3, dash="dash"),
                        marker=dict(size=8, symbol="diamond"),
                    ))
                grafana_layout(fig, "Actual vs Predicted SOH Range", height=280)
                st.plotly_chart(fig, use_container_width=True)

            with col_r:
                st.dataframe(
                    df_soh_bias.round(3),
                    hide_index=True, use_container_width=True,
                )

    # ── tab: feature importance ────────────────────────────────
    with tab4:
        if df_soh_feat_imp is not None:
            top_n = st.slider("Top N features", 5, len(df_soh_feat_imp), 10, key="soh_topn")
            imp = df_soh_feat_imp.sort_values("importance", ascending=True).tail(top_n)

            fig = go.Figure(go.Bar(
                y=imp["feature"], x=imp["importance"],
                orientation="h",
                marker_color=[
                    GRAFANA_GREEN if v > 0.1 else GRAFANA_BLUE if v > 0.01 else GRAFANA_TEXT_DIM
                    for v in imp["importance"]
                ],
                text=[f"{v:.4f}" for v in imp["importance"]],
                textposition="outside",
                textfont=dict(color=GRAFANA_TEXT, size=11),
            ))
            grafana_layout(fig, "Feature Importance (XGBoost — All Cells)", height=max(300, top_n * 32), showlegend=False)
            fig.update_layout(xaxis_title="Importance", yaxis_title="")
            st.plotly_chart(fig, use_container_width=True)


# ═══════════════════════════════════════════════════════════════
#  PAGE: RUL ANALYSIS
# ═══════════════════════════════════════════════════════════════
elif page == "RUL Analysis":

    st.markdown(f"""
    <div class="gf-topbar">
        <div class="gf-topbar-left">
            <span class="gf-topbar-title">Remaining Useful Life · Analysis</span>
            <span class="gf-badge gf-badge-yellow">EXPERIMENTAL</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    tab1, tab2, tab3 = st.tabs(["Predictions", "Model Scores", "Degradation Rates"])

    # ── tab: RUL predictions ──────────────────────────────────
    with tab1:
        if df_rul_pred is not None:
            sel = st.multiselect(
                "Select cells", cells, default=cells, key="rul_cells"
            )
            for cell in sel:
                sub = df_rul_pred[df_rul_pred["cell_id"] == cell].sort_values("cycle")
                rul_col = "rul_cycles_80" if "rul_cycles_80" in sub.columns else "rul_cycles"
                pred_col = "predicted_rul" if "predicted_rul" in sub.columns else None
                if rul_col not in sub.columns:
                    continue

                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=sub["cycle"], y=sub[rul_col],
                    mode="lines", name="Actual RUL",
                    line=dict(color=GRAFANA_TEXT, width=2),
                ))
                if pred_col and pred_col in sub.columns:
                    fig.add_trace(go.Scatter(
                        x=sub["cycle"], y=sub[pred_col],
                        mode="lines", name="Predicted RUL",
                        line=dict(color=CELL_COLORS[cell], width=2, dash="dash"),
                    ))
                fig.add_hline(y=0, line_color=GRAFANA_RED, line_width=1, line_dash="dot",
                              annotation_text="End of Life",
                              annotation_font_color=GRAFANA_RED,
                              annotation_font_size=10)
                grafana_layout(fig, f"{cell} · RUL (cycles to 80% SOH)", height=340)
                st.plotly_chart(fig, use_container_width=True)

    # ── tab: model scores ──────────────────────────────────────
    with tab2:
        if df_rul_results is not None:
            st.markdown("#### Leave-One-Cell-Out Scores")

            # group by model+drop
            drop_col = "drop" if "drop" in df_rul_results.columns else None
            if drop_col:
                grouped = df_rul_results.groupby(["model", drop_col]).agg(
                    MAE=("MAE", "mean"), RMSE=("RMSE", "mean"), R2=("R2", "mean")
                ).reset_index().sort_values("MAE")
                grouped["label"] = grouped["model"] + " (" + grouped[drop_col].astype(str) + ")"
            else:
                grouped = df_rul_results.groupby("model").agg(
                    MAE=("MAE", "mean"), RMSE=("RMSE", "mean"), R2=("R2", "mean")
                ).reset_index().sort_values("MAE")
                grouped["label"] = grouped["model"]

            fig = go.Figure(go.Bar(
                x=grouped["label"], y=grouped["MAE"],
                marker_color=[GRAFANA_SERIES[i % len(GRAFANA_SERIES)] for i in range(len(grouped))],
                text=[f"{v:.1f}" for v in grouped["MAE"]],
                textposition="outside",
                textfont=dict(color=GRAFANA_TEXT, size=11),
            ))
            grafana_layout(fig, "Mean MAE by Model (cycles)", showlegend=False)
            fig.update_layout(yaxis_title="MAE (cycles)")
            st.plotly_chart(fig, use_container_width=True)

            # null baselines comparison
            if df_rul_null is not None:
                null_agg = df_rul_null.groupby("baseline")["MAE"].mean().sort_values()
                st.markdown("#### Null Baselines")
                fig = go.Figure(go.Bar(
                    x=null_agg.index, y=null_agg.values,
                    marker_color=GRAFANA_TEXT_DIM,
                    text=[f"{v:.1f}" for v in null_agg.values],
                    textposition="outside",
                    textfont=dict(color=GRAFANA_TEXT, size=11),
                ))
                grafana_layout(fig, "Constant Predictor Baselines", height=300, showlegend=False)
                fig.update_layout(yaxis_title="MAE (cycles)")
                st.plotly_chart(fig, use_container_width=True)

            st.markdown("#### Raw Results")
            st.dataframe(df_rul_results.round(3), hide_index=True, use_container_width=True)

    # ── tab: degradation rates ─────────────────────────────────
    with tab3:
        if df_rul_degrad is not None:
            st.markdown("#### Battery Degradation Characteristics")

            col_l, col_r = st.columns(2)
            with col_l:
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=df_rul_degrad["cell_id"],
                    y=df_rul_degrad["fade_pct_per_cycle"].abs(),
                    name="Full-life rate",
                    marker_color=GRAFANA_BLUE,
                    text=[f"{abs(v):.4f}" for v in df_rul_degrad["fade_pct_per_cycle"]],
                    textposition="outside",
                    textfont=dict(color=GRAFANA_TEXT, size=11),
                ))
                fig.add_trace(go.Bar(
                    x=df_rul_degrad["cell_id"],
                    y=df_rul_degrad["early_fade_pct_per_cycle"].abs(),
                    name="Early-life rate",
                    marker_color=GRAFANA_ORANGE,
                    text=[f"{abs(v):.4f}" for v in df_rul_degrad["early_fade_pct_per_cycle"]],
                    textposition="outside",
                    textfont=dict(color=GRAFANA_TEXT, size=11),
                ))
                grafana_layout(fig, "Fade Rate (%/cycle)", height=340)
                fig.update_layout(barmode="group", yaxis_title="Fade (%/cycle)")
                st.plotly_chart(fig, use_container_width=True)

            with col_r:
                fig = go.Figure()
                fig.add_trace(go.Bar(
                    x=df_rul_degrad["cell_id"],
                    y=df_rul_degrad["eol_cycle"],
                    marker_color=[CELL_COLORS[c] for c in df_rul_degrad["cell_id"]],
                    text=df_rul_degrad["eol_cycle"].astype(int).astype(str),
                    textposition="outside",
                    textfont=dict(color=GRAFANA_TEXT, size=12),
                ))
                grafana_layout(fig, "End-of-Life Cycle (80% SOH)", height=340, showlegend=False)
                fig.update_layout(yaxis_title="Cycle")
                st.plotly_chart(fig, use_container_width=True)

            st.dataframe(df_rul_degrad.round(4), hide_index=True, use_container_width=True)


# ═══════════════════════════════════════════════════════════════
#  PAGE: FEATURE EXPLORER
# ═══════════════════════════════════════════════════════════════
elif page == "Feature Explorer":

    st.markdown(f"""
    <div class="gf-topbar">
        <div class="gf-topbar-left">
            <span class="gf-topbar-title">Feature Explorer</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if df_ml is not None:
        numeric_cols = df_ml.select_dtypes(include=[np.number]).columns.tolist()
        non_id_cols = [c for c in numeric_cols if c not in ["cycle"]]

        tab1, tab2, tab3 = st.tabs(["Time Series", "Correlation Matrix", "Distributions"])

        with tab1:
            feature = st.selectbox("Feature", non_id_cols, index=non_id_cols.index("soh") if "soh" in non_id_cols else 0)
            fig = go.Figure()
            for cell in cells:
                sub = df_ml[df_ml["cell_id"] == cell].sort_values("cycle")
                fig.add_trace(go.Scatter(
                    x=sub["cycle"], y=sub[feature],
                    mode="lines", name=cell,
                    line=dict(color=CELL_COLORS[cell], width=1.5),
                ))
            grafana_layout(fig, f"{feature} over Cycles")
            st.plotly_chart(fig, use_container_width=True)

        with tab2:
            corr_features = st.multiselect(
                "Select features for correlation",
                non_id_cols,
                default=[c for c in ["soh", "capacity_ah", "voltage_mean", "temperature_mean",
                                     "resistance_proxy_ohm", "discharge_duration_s", "cycle"] if c in non_id_cols],
                key="corr_feats",
            )
            if len(corr_features) > 1:
                corr = df_ml[corr_features].corr()
                fig = go.Figure(go.Heatmap(
                    z=corr.values,
                    x=corr.columns, y=corr.columns,
                    colorscale=[
                        [0, GRAFANA_RED], [0.5, GRAFANA_PANEL], [1, GRAFANA_GREEN]
                    ],
                    zmin=-1, zmax=1,
                    text=corr.round(2).values,
                    texttemplate="%{text}",
                    textfont=dict(size=10, color=GRAFANA_TEXT),
                ))
                grafana_layout(fig, "Feature Correlation Matrix", height=max(350, len(corr_features) * 40))
                st.plotly_chart(fig, use_container_width=True)

        with tab3:
            dist_feat = st.selectbox("Feature", non_id_cols, key="dist_feat",
                                     index=non_id_cols.index("soh") if "soh" in non_id_cols else 0)
            fig = go.Figure()
            for cell in cells:
                sub = df_ml[df_ml["cell_id"] == cell]
                fig.add_trace(go.Histogram(
                    x=sub[dist_feat], name=cell,
                    marker_color=CELL_COLORS[cell],
                    opacity=0.65,
                    nbinsx=30,
                ))
            grafana_layout(fig, f"Distribution of {dist_feat}")
            fig.update_layout(barmode="overlay", xaxis_title=dist_feat, yaxis_title="Count")
            st.plotly_chart(fig, use_container_width=True)


# ═══════════════════════════════════════════════════════════════
#  PAGE: DATA INSPECTOR
# ═══════════════════════════════════════════════════════════════
elif page == "Data Inspector":

    st.markdown(f"""
    <div class="gf-topbar">
        <div class="gf-topbar-left">
            <span class="gf-topbar-title">Data Inspector</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    available = {}
    for name in sorted(os.listdir(DATA)):
        if name.endswith(".csv"):
            available[name] = os.path.join(DATA, name)

    selected = st.selectbox("Dataset", list(available.keys()))

    if selected:
        df_inspect = pd.read_csv(available[selected])

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Rows", f"{len(df_inspect):,}")
        c2.metric("Columns", len(df_inspect.columns))
        c3.metric("Nulls", int(df_inspect.isnull().sum().sum()))
        c4.metric("Size", f"{os.path.getsize(available[selected]) / 1024:.1f} KB")

        tab1, tab2, tab3 = st.tabs(["Preview", "Statistics", "Schema"])

        with tab1:
            n_rows = st.slider("Rows to display", 10, min(500, len(df_inspect)), 50, key="inspect_rows")
            st.dataframe(df_inspect.head(n_rows), hide_index=True, use_container_width=True)

        with tab2:
            st.dataframe(df_inspect.describe().T.round(4), use_container_width=True)

        with tab3:
            schema = pd.DataFrame({
                "Column": df_inspect.columns,
                "Type": [str(t) for t in df_inspect.dtypes],
                "Non-Null": [int(df_inspect[c].notna().sum()) for c in df_inspect.columns],
                "Null %": [round(df_inspect[c].isna().mean() * 100, 2) for c in df_inspect.columns],
                "Unique": [df_inspect[c].nunique() for c in df_inspect.columns],
            })
            st.dataframe(schema, hide_index=True, use_container_width=True)


# ═══════════════════════════════════════════════════════════════
#  PAGE: PIPELINE LOGS
# ═══════════════════════════════════════════════════════════════
elif page == "Pipeline Logs":

    st.markdown(f"""
    <div class="gf-topbar">
        <div class="gf-topbar-left">
            <span class="gf-topbar-title">Pipeline Execution Logs</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # generate structured logs from the data
    logs = []

    def add_log(ts, level, source, msg):
        logs.append({"timestamp": ts, "level": level, "source": source, "message": msg})

    now = datetime.datetime.now()

    # data loading logs
    if df_raw is not None:
        add_log(now.strftime("%H:%M:%S.%f")[:-3], "INFO", "load_data",
                f"Loaded {len(df_raw)} raw cycle features from {len(df_raw['cell_id'].unique())} cells")
        for cell in cells:
            n = len(df_raw[df_raw["cell_id"] == cell])
            add_log(now.strftime("%H:%M:%S.%f")[:-3], "INFO", "load_data",
                    f"  {cell}: {n} discharge cycles extracted")

    if df_ml is not None:
        add_log(now.strftime("%H:%M:%S.%f")[:-3], "INFO", "preprocessing",
                f"ML dataset assembled: {df_ml.shape[0]} rows × {df_ml.shape[1]} columns")
        null_pct = df_ml.isnull().mean().mean() * 100
        add_log(now.strftime("%H:%M:%S.%f")[:-3], "INFO" if null_pct < 5 else "WARN", "preprocessing",
                f"Null rate: {null_pct:.2f}%")

    # SOH training logs
    if df_soh_baselines is not None:
        add_log(now.strftime("%H:%M:%S.%f")[:-3], "INFO", "train_soh",
                "Baseline ladder computed (leave-one-cell-out)")
        best = df_soh_baselines.groupby("model")["MAE"].mean().sort_values()
        for model, mae in best.items():
            add_log(now.strftime("%H:%M:%S.%f")[:-3], "INFO", "train_soh",
                    f"  {model}: MAE = {mae:.4f}")

    if df_soh_nested is not None:
        nested_mae = df_soh_nested["MAE"].mean()
        add_log(now.strftime("%H:%M:%S.%f")[:-3], "INFO", "train_soh",
                f"Nested XGBoost search complete: mean MAE = {nested_mae:.4f}")
        for _, row in df_soh_nested.iterrows():
            add_log(now.strftime("%H:%M:%S.%f")[:-3], "INFO", "train_soh",
                    f"  {row['test_cell']}: MAE={row['MAE']:.4f} RMSE={row['RMSE']:.4f} R²={row['R2']:.4f}")

    if df_soh_feat_imp is not None:
        top3 = df_soh_feat_imp.nlargest(3, "importance")
        add_log(now.strftime("%H:%M:%S.%f")[:-3], "INFO", "train_soh",
                f"Top features: {', '.join(top3['feature'].tolist())}")

    # SOH model save
    soh_models = [f for f in df_models["file"].tolist() if f.startswith("soh_")]
    if soh_models:
        add_log(now.strftime("%H:%M:%S.%f")[:-3], "INFO", "train_soh",
                f"Saved {len(soh_models)} SOH models to models/")

    # RUL training logs
    if df_rul_results is not None:
        add_log(now.strftime("%H:%M:%S.%f")[:-3], "INFO", "train_rul",
                f"RUL model evaluation complete: {len(df_rul_results)} runs")
        best_rul = df_rul_results.groupby("model")["MAE"].mean().sort_values()
        for model, mae in best_rul.head(3).items():
            add_log(now.strftime("%H:%M:%S.%f")[:-3], "INFO", "train_rul",
                    f"  {model}: MAE = {mae:.1f} cycles")

    if df_rul_degrad is not None:
        add_log(now.strftime("%H:%M:%S.%f")[:-3], "INFO", "train_rul",
                "Degradation rate analysis complete")
        for _, row in df_rul_degrad.iterrows():
            add_log(now.strftime("%H:%M:%S.%f")[:-3], "INFO", "train_rul",
                    f"  {row['cell_id']}: EOL @ cycle {int(row['eol_cycle'])}, "
                    f"fade = {abs(row['fade_pct_per_cycle']):.4f}%/cycle")

    rul_models = [f for f in df_models["file"].tolist() if f.startswith("rul_")]
    if rul_models:
        add_log(now.strftime("%H:%M:%S.%f")[:-3], "INFO", "train_rul",
                f"Saved {len(rul_models)} RUL models to models/")

    add_log(now.strftime("%H:%M:%S.%f")[:-3], "INFO", "pipeline",
            "Pipeline execution complete. All artifacts saved.")

    # filter controls
    col_f1, col_f2 = st.columns([1, 3])
    with col_f1:
        level_filter = st.multiselect("Level", ["INFO", "WARN", "ERR"], default=["INFO", "WARN", "ERR"])
    with col_f2:
        source_filter = st.multiselect("Source", sorted(set(l["source"] for l in logs)),
                                       default=sorted(set(l["source"] for l in logs)))

    filtered = [l for l in logs if l["level"] in level_filter and l["source"] in source_filter]

    # render log panel
    log_lines = []
    for entry in filtered:
        level_class = "info" if entry["level"] == "INFO" else "warn" if entry["level"] == "WARN" else "err"
        log_lines.append(
            f'<span class="ts">{entry["timestamp"]}</span> '
            f'<span class="{level_class}">[{entry["level"]:>4}]</span> '
            f'<span class="ts">{entry["source"]:>14}</span> │ '
            f'{entry["message"]}'
        )

    st.markdown(
        f'<div class="gf-log">{"<br>".join(log_lines)}</div>',
        unsafe_allow_html=True,
    )

    # summary stats
    st.markdown("")
    c1, c2, c3 = st.columns(3)
    c1.metric("Total Log Entries", len(logs))
    c2.metric("Warnings", sum(1 for l in logs if l["level"] == "WARN"))
    c3.metric("Errors", sum(1 for l in logs if l["level"] == "ERR"))

    # source code viewer
    st.markdown("---")
    st.markdown("### Source Code Viewer")
    src_file = st.selectbox("File", df_sources["file"].tolist() if df_sources is not None else [])
    if src_file:
        fpath = os.path.join(SRC, src_file)
        with open(fpath) as f:
            code = f.read()
        st.code(code, language="python", line_numbers=True)
