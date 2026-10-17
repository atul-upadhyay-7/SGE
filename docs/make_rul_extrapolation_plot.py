"""Draws docs/rul_extrapolation_B0005_B0007.png.

Run: python docs/make_rul_extrapolation_plot.py

The fade-rate extrapolation reads only a cell's first 20 measured
discharges and fits one straight line to them, then extends that line
to the 80% retirement threshold. This figure shows the two G1 cells
where that fails: B0005 and B0007 fade almost flat over their opening
20 discharges, so the fitted line is far too shallow and crosses 80%
well past the true knee. The marked fit window, the extrapolated line,
the true end of life and the implied one are all drawn so the miss is
visible rather than asserted.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import rul_cross_group as RG  # noqa: E402
from preprocessing import EOL_SOH_THRESHOLD  # noqa: E402

OUT = ROOT / "docs" / "rul_extrapolation_B0005_B0007.png"
SWEEP = ROOT / "data" / "processed" / "rul_extrapolation_window_sweep.csv"

CELLS = ["B0005", "B0007"]

dataset = RG.rul_dataset(RG.CLEAN, RG.resolve_raw_dir())
sweep = pd.read_csv(SWEEP)

fig, axes = plt.subplots(1, 2, figsize=(13, 5))

for ax, cell in zip(axes, CELLS):

    frame = dataset[dataset["cell_id"] == cell].sort_values("cycle")
    soh = frame["soh"].to_numpy(dtype=float)
    x = np.arange(1, len(soh) + 1)

    eol = RG._eol_index(soh)
    window = int(RG.EXTRAP_WINDOW)
    implied = RG._implied_eol(soh, EOL_SOH_THRESHOLD, window)

    slope, intercept = np.polyfit(np.arange(window), soh[:window], 1)

    mae = float(
        sweep.loc[
            (sweep["cell_id"] == cell) & (sweep["window"] == window), "mae"
        ].iloc[0]
    )

    ax.plot(x, soh, color="#111111", lw=1.6, label="Measured SOH")
    ax.axvspan(
        0.5, window + 0.5, color="#1f77b4", alpha=0.12,
        label=f"First {window} discharges (fit window)",
    )
    ax.scatter(
        x[:window], soh[:window], color="#1f77b4", s=18, zorder=4,
    )

    line_x = np.array([0.0, implied * 1.05])
    ax.plot(
        line_x, slope * line_x + intercept, color="#d62728", lw=1.6,
        ls="--", label="Extrapolated fade line",
    )

    ax.axhline(
        EOL_SOH_THRESHOLD, color="#666666", lw=1.0, ls=":",
        label="Retirement threshold (80%)",
    )
    ax.axvline(
        eol + 1, color="#2ca02c", lw=1.3, ls="-",
        label=f"True EOL (discharge {eol + 1})",
    )
    ax.axvline(
        implied, color="#d62728", lw=1.3, ls="-",
        label=f"Extrapolated EOL ({implied:.0f})",
    )

    ax.annotate(
        "", xy=(implied, 90), xytext=(eol + 1, 90),
        arrowprops=dict(arrowstyle="<->", color="#d62728", lw=1.2),
    )
    ax.text(
        (eol + 1 + implied) / 2, 91.5,
        f"misses the knee by {implied - (eol + 1):.0f} discharges",
        color="#d62728", ha="center", fontsize=8.5,
    )

    ax.set_xlim(0, max(implied * 1.05, eol * 1.2))
    ax.set_ylim(70, 104)
    ax.set_xlabel("Discharge index")
    ax.set_ylabel("SOH (%)")
    ax.set_title(
        f"{cell}: flat early fade, line missed the knee\n"
        f"extrapolation MAE {mae:.1f} discharges"
    )
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, loc="lower left")

fig.suptitle(
    "Why the fade-rate extrapolation and trajectory matching disagree",
    fontsize=12,
)
fig.tight_layout()
fig.savefig(OUT, dpi=150)
print(f"wrote {OUT.relative_to(ROOT)}")
