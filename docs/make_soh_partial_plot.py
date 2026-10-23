"""Draws docs/soh_partial_B0053.png.

Run: python docs/make_soh_partial_plot.py

Reads data/processed/soh_partial_predictions_B0053.csv (written by
src/run_experiments.py) and plots predicted against true SOH over
discharge index for B0053 — the one held-out G9 cell, scored by the
partial-window model trained on G1+G3. The point of the figure is the
shape: the model sits near the level but does not follow the fade,
which is what the negative R² measures.
"""
import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
from pathlib import Path

import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "processed" / "soh_partial_predictions_B0053.csv"
OUT = ROOT / "docs" / "soh_partial_B0053.png"

df = pd.read_csv(CSV).sort_values("discharge_index")

true = df["soh"].to_numpy(float)
x = df["discharge_index"].to_numpy(float)


def mae(pred):
    return float(np.mean(np.abs(pred - true)))


def r2(pred):
    return 1.0 - float(np.sum((pred - true) ** 2)) / float(
        np.sum((true - true.mean()) ** 2)
    )


series = [
    ("True SOH (measured)", true, "#111111", 2.2, "-"),
    (
        "Partial-window XGBoost",
        df["predicted_xgboost"].to_numpy(float),
        "#1f77b4",
        1.8,
        "-",
    ),
    (
        "Partial-window ElasticNet",
        df["predicted_elasticnet"].to_numpy(float),
        "#2ca02c",
        1.6,
        "-",
    ),
    (
        "Window formula (physics)",
        df["window_formula"].to_numpy(float),
        "#d62728",
        1.4,
        "--",
    ),
]

fig, ax = plt.subplots(figsize=(9, 5))

for label, y, color, lw, ls in series:

    suffix = ""
    if label != "True SOH (measured)":
        suffix = f"  (MAE {mae(y):.2f}%, R² {r2(y):.2f})"

    ax.plot(x, y, color=color, lw=lw, ls=ls, label=label + suffix)

ax.axhline(
    true.mean(),
    color="#888888",
    lw=1.0,
    ls=":",
    label=f"Constant mean ({true.mean():.2f}%)",
)

ax.set_xlabel("Discharge index")
ax.set_ylabel("SOH (%)")
ax.set_title(
    "B0053 (G9): predicted vs true SOH over discharge index\n"
    "The model tracks the level but not the fade shape (R² < 0)"
)
ax.grid(alpha=0.25)
ax.legend(fontsize=8.5, loc="best")

fig.tight_layout()
fig.savefig(OUT, dpi=150)
print(f"wrote {OUT.relative_to(ROOT)}")
