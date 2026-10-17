"""Read models for the historical query APIs (Phase 3)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TelemetrySampleRead(BaseModel):
    """A persisted telemetry sample as returned to clients (FR-06)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: str
    message_id: str
    measured_at: datetime
    received_at: datetime
    pack_voltage: float | None
    current: float
    temperature: float
    cell_voltages: list[float]
    additional_data: dict | None
    quality_flags: dict | None


class TelemetryPage(BaseModel):
    """One page of samples in measured-at order.

    ``next_cursor`` is opaque; pass it back verbatim as ``cursor`` to fetch the
    following page. It is ``null`` when the last page has been reached.
    """

    device_id: str
    order: str
    limit: int
    count: int
    next_cursor: str | None
    items: list[TelemetrySampleRead]


class TelemetrySummary(BaseModel):
    """Cheap aggregate for a device: coverage, freshness and data quality."""

    device_id: str
    status: str
    last_seen_at: datetime | None
    sample_count: int
    first_measured_at: datetime | None
    last_measured_at: datetime | None
    rejection_count: int
    rejections_by_reason: dict[str, int]
