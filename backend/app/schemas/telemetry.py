"""Telemetry payload contract, validation and the legacy adapter (FR-03, FR-04).

The PRD payload (``docs``/PRD §Payload) is canonical. Structural validation
lives in the Pydantic model; semantic validation against the device's
declared capabilities and the configured physical ranges is applied by the
ingestion service. A single failure never raises past this module's
exception type, so both transports (MQTT and HTTP) can share one path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

DEFAULT_SCHEMA_VERSION = 1


class TelemetryRejected(Exception):
    """A payload failed ingestion. ``reason`` is a stable, filterable code."""

    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class TelemetryPayload(BaseModel):
    """The canonical telemetry contract (FR-03).

    ``allow_inf_nan=False`` rejects NaN/Infinity, and ``extra="allow"``
    preserves optional/documented extension fields so they can be persisted
    in ``additional_data`` without a schema change.
    """

    model_config = {
        "extra": "allow",
        "allow_inf_nan": False,
        "str_strip_whitespace": True,
    }

    schema_version: int = Field(ge=1)
    message_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=64)
    measured_at: datetime
    current: float
    temperature: float
    # Bounded so a malformed/hostile payload cannot allocate unbounded
    # memory before the per-device cell-count check runs (devices declare
    # at most 64 cells; this leaves generous headroom for pack layouts).
    cell_voltages: list[float] = Field(max_length=1024)
    pack_voltage: float | None = None

    @property
    def extras(self) -> dict[str, Any]:
        """Unknown/optional fields, stored verbatim in ``additional_data``."""
        return dict(self.model_extra or {})

    @field_validator("measured_at")
    @classmethod
    def _require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("measured_at must be timezone-aware (UTC)")
        return value.astimezone(UTC)

    @field_validator("cell_voltages")
    @classmethod
    def _non_empty_finite(cls, value: list[float]) -> list[float]:
        if not value:
            raise ValueError("cell_voltages must contain at least one cell")
        if any(not math.isfinite(v) for v in value):
            raise ValueError("cell_voltages must be finite")
        return value


def looks_like_legacy(data: dict[str, Any]) -> bool:
    """A pre-PRD firmware frame carries a single ``voltage`` and no cells."""
    return "cell_voltages" not in data and "voltage" in data


def adapt_legacy_payload(data: dict[str, Any], device_id: str) -> dict[str, Any]:
    """Map a pre-PRD per-cell frame onto the canonical PRD contract.

    The legacy firmware published one cell per message with an epoch
    timestamp. The adapter synthesises a stable ``message_id`` from the
    device and timestamp so re-delivery stays idempotent. A legacy device
    must be registered with ``cell_count`` of 1 to validate.
    """
    timestamp = float(data["timestamp"])
    measured_at = datetime.fromtimestamp(timestamp, tz=UTC).isoformat()
    return {
        "schema_version": 1,
        "message_id": f"{device_id}-{int(timestamp * 1000)}",
        "device_id": device_id,
        "measured_at": measured_at,
        "current": data.get("current"),
        "temperature": data.get("temperature"),
        "cell_voltages": [float(data["voltage"])],
    }


@dataclass(slots=True)
class ValidatedSample:
    """A payload that passed every check; ready to persist (FR-05)."""

    device_id: str
    message_id: str
    measured_at: datetime
    current: float
    temperature: float
    cell_voltages: list[float]
    pack_voltage: float | None = None
    additional_data: dict[str, Any] | None = None
    quality_flags: dict[str, Any] | None = None


@dataclass(slots=True)
class RejectionRecord:
    """A quarantined payload with an auditable reason (FR-02, FR-04)."""

    reason: str
    detail: str | None = None
    device_id: str | None = None
    message_id: str | None = None
    topic: str | None = None
    source: str = "mqtt"
    raw_payload: dict[str, Any] | None = field(default=None)
