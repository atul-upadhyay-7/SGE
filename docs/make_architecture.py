"""Draws docs/architecture.png. Run: python docs/make_architecture.py"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

fig, ax = plt.subplots(figsize=(14, 7.2))
ax.set_xlim(0, 14); ax.set_ylim(0, 7.2); ax.axis("off")

def box(x, y, w, h, title, lines, fc, ec="#333"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.04,rounding_size=0.12",
                                fc=fc, ec=ec, lw=1.4))
    ax.text(x + w / 2, y + h - 0.28, title, ha="center", va="center", fontsize=10.5, weight="bold")
    ax.text(x + w / 2, y + (h - 0.45) / 2, "\n".join(lines), ha="center", va="center", fontsize=8.3)

def arrow(x1, y1, x2, y2, label="", dy=0.16):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", lw=1.5, color="#222"))
    if label:
        ax.text((x1 + x2) / 2, (y1 + y2) / 2 + dy, label, ha="center", fontsize=7.8, color="#222")

# hardware
box(0.2, 2.4, 2.6, 3.2, "3S pack (3 x 18650)",
    ["cell taps", "INA219: pack current", "NTC x3: temperature", "ESP32 + ADS1115", "(firmware: untested on HW)"], "#fde9c9")
box(0.2, 0.2, 2.6, 1.5, "Demo stand-in", ["src/pack/publisher.py", "NASA cells replay", "+ labelled synthetic faults"], "#eeeeee")
# broker
box(3.7, 0.9, 2.0, 4.8, "Mosquitto", ["MQTT broker", "", "bms/CELLn/telemetry", "bms/CELLn/soh", "bms/pack/PACK1/state"], "#dcebf7")
# soh
box(6.7, 4.0, 2.6, 2.2, "SOH service x3",
    ["cycle tracker + DCR", "causal features (past only)", "XGBoost SOH model", "run_pack.py"], "#e1f2dc")
# bridge
box(6.7, 0.5, 2.6, 2.7, "Pack monitor",
    ["bridge.py + PackMonitor", "imbalance, weak cell,", "resistance spike, thermal,", "Isolation Forest,", "cycles to 80% retirement"], "#e1f2dc")
# store + api
box(10.1, 3.6, 1.8, 2.0, "live_pack.db", ["SQLite", "cells, history,", "alerts, status"], "#f3e3f5")
box(10.1, 0.6, 1.8, 2.0, "JSON API", ["Flask :8099", "dashboard/", "grafana_api.py"], "#f3e3f5")
box(12.3, 1.8, 1.5, 3.2, "Grafana", ["Battery Pack", "Live dashboard:", "cell health,", "alerts,", "retirement"], "#fbe0e0")

arrow(2.8, 4.0, 3.7, 4.0, "JSON, 1 s", dy=0.12)
arrow(2.8, 1.0, 3.7, 1.6, "")
arrow(5.7, 5.5, 6.7, 5.5, "telemetry", dy=0.12)
arrow(6.7, 4.5, 5.7, 4.5, "soh", dy=-0.28)
arrow(5.7, 2.9, 6.7, 2.9, "soh", dy=0.12)
arrow(6.7, 1.0, 5.7, 1.0, "state", dy=0.12)
arrow(9.3, 2.2, 10.1, 4.0, "writes", dy=0.1)
arrow(11.0, 3.6, 11.0, 2.6, "")
arrow(11.9, 1.6, 12.3, 2.4, "queries", dy=-0.35)
ax.text(7.0, 7.0, "Battery pack health and predictive maintenance: data flow", ha="center", fontsize=12, weight="bold")
fig.savefig("docs/architecture.png", dpi=130, bbox_inches="tight")
