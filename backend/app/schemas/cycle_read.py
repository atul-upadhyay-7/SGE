"""Read models for aggregated cycles and SoH predictions (Phase 4)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CycleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: str
    cycle_number: int
    start_time: datetime
    end_time: datetime | None = None
    capacity_ah: float | None = None
    reference_capacity_ah: float | None = None
    measured_soh: float | None = None
    feature_schema_version: str


class CyclePage(BaseModel):
    device_id: str
    count: int
    items: list[CycleRead]


class SohPredictionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: str
    cycle_id: int | None = None
    predicted_soh: float
    measured_soh: float | None = None
    model_version: str
    feature_schema_version: str
    predicted_at: datetime
    input_quality_status: str


class SohPage(BaseModel):
    device_id: str
    count: int
    items: list[SohPredictionRead]


class AggregationResult(BaseModel):
    device_id: str
    samples: int
    cycles: int
    predictions: int
    refused: int
