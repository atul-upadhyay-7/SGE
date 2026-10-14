"""
evaluate.py
-----------
Train the pack Isolation Forest and measure the alert engine.

Packs: every 3-cell combination of the four NASA cells, SOH from
the leave-one-cell-out model (the leakage-free Mode B output).

False alarms: run each healthy pack and count the discharges on
which any rule or ML flag at warn level or above fires, counted
only while all three cells are still alive.

Detection: inject each synthetic fault (resistance_spike,
cell_sag, thermal) into each cell of each pack at several start
points; record whether a matching alert fired and how many
discharges after the start.

The Isolation Forest is fitted leave-one-pack-out: trained on the
healthy features of the other packs and scored on this one, so no
pack is both training data and test data.

These are SYNTHETIC faults on real healthy NASA behaviour. The
numbers say the engine reacts to the disturbances it is designed
for; they do not estimate accuracy on real hardware faults.
"""

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pack.monitor import (  # noqa: E402
    PackMonitor,
    fit_iforest,
    save_iforest,
)
from pack.simulate import FAULTS, inject, pack_frames, replay  # noqa: E402
from rul_trajectory import TrajectoryRul  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
PROC = ROOT / "data" / "processed"

# alert codes that count as detecting each fault
EXPECT = {
    "resistance_spike": {"RESISTANCE_SPIKE"},
    "cell_sag": {"IMBALANCE", "WEAK_CELL"},
    "thermal": {"THERMAL", "THERMAL_RATE"},
}

STARTS = (30, 50)


def audited_cells():
    audit = pd.read_csv(PROC / "cell_audit.csv")

    return sorted(
        audit.loc[audit["keep"].astype(bool), "cell_id"].tolist()
    )


def load_df(cells=None):
    """
    Load the clean dataset, restricted to audited cells that also
    have a committed per-cell SOH prediction.

    Only cells with both an audit keep=True and a prediction file
    are usable by the pack monitor: a cell without an audit verdict
    is not trusted, and a cell without a prediction has no Mode B
    SOH stream to monitor. This is what keeps the stage finite on
    the 19-cell dataset (a naive all-cells choice is C(19,3)=969
    packs and never terminates in the pipeline budget).
    """

    df = pd.read_csv(PROC / "nasa_ml_dataset_clean.csv")

    pred = []
    skipped = []

    for c in sorted(df["cell_id"].unique()):
        path = PROC / f"soh_predictions_{c}.csv"
        if not path.exists():
            skipped.append(c)
            continue
