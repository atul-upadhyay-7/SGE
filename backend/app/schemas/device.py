"""Device registration schemas (FR-01)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DeviceCreate(BaseModel):
    """Request body for registering a device.

    ``cell_count`` is required and authoritative: it is the parameter the
    ingestion validator checks each telemetry payload against (FR-04).
    """

    device_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    cell_count: int = Field(ge=1, le=64)
    sampling_frequency_hz: float = Field(default=1.0, gt=0, le=100.0)
    status: str = Field(default="active", pattern="^(active|inactive|retired)$")
    credential_ref: str | None = Field(default=None, max_length=255)


class DeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: str
    name: str
    cell_count: int
    sampling_frequency_hz: float
    status: str
    created_at: datetime | None = None
    last_seen_at: datetime | None = None
