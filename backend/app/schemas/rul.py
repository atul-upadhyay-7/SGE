"""Response schemas for the RUL/EOL endpoint (FR-08)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SohPointRead(BaseModel):
    """One predicted SoH value that fed the trajectory match."""

    predicted_soh: float = Field(description="Predicted state of health (%).")
    predicted_at: datetime | None = None
    cycle_id: int | None = None


class RulRead(BaseModel):
    """Remaining useful life for one device.

    ``status`` is the field to branch on:

    * ``ok``              — ``rul_discharges`` and the band are meaningful;
    * ``retired``         — smoothed SoH is already at/below the threshold;
    * ``no_predictions``  — the device has no stored SoH yet;
    * ``no_history``      — nothing to match (defensive; mirrors ``ok`` path);
    * ``no_references``   — no reference cell ever reached the threshold.

    ``p10_discharges``/``p90_discharges`` are the spread of the per-reference
    remaining lives, *not* a calibrated predictive interval.
    """

    device_id: str
    status: str
    rul_discharges: int | None = None
    p10_discharges: float | None = None
    p90_discharges: float | None = None
    threshold_soh_pct: float
    reference_count: int
    history_count: int
    total_predictions: int
    basis: str = "reference_trajectory_matching"
    history: list[SohPointRead] = Field(default_factory=list)
