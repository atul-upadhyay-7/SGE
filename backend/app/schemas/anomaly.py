"""Read/write models for the anomaly APIs (CR §3, §4)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["info", "warning", "critical"]
AnomalyStatus = Literal["open", "acknowledged", "investigating", "resolved"]
ActionType = Literal["acknowledge", "note", "corrective_action", "resolve"]


class AnomalyEventRead(BaseModel):
    """One diagnosed anomaly episode (CR §3)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: str
    anomaly_type: str
    severity: str
    affected_cell: int | None = None
    detected_at: datetime
    last_detected_at: datetime
    ended_at: datetime | None = None
    observed_value: float | None = None
    expected_value: float | None = None
    threshold: float | None = None
    duration_seconds: float
    status: str
    diagnostic_evidence: dict | None = None
    likely_causes: list | None = None
    recommended_actions: list | None = None
    rule_version: str
    created_at: datetime
    updated_at: datetime


class AnomalyPage(BaseModel):
    device_id: str | None = None
    count: int
    limit: int
    offset: int
    total: int
    items: list[AnomalyEventRead]


class AnomalyActionRead(BaseModel):
    """One entry in an anomaly's audit trail (CR §3)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    anomaly_id: int
    action_type: str
    actor: str | None = None
    note: str | None = None
    evidence: dict | None = None
    created_at: datetime


class AnomalyActionCreate(BaseModel):
    """Body for ``POST /anomalies/{id}/actions`` (CR §4)."""

    action_type: ActionType
    actor: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=2048)
    evidence: dict | None = None


class AnomalyAcknowledge(BaseModel):
    """Body for ``POST /anomalies/{id}/acknowledge`` (CR §4)."""

    actor: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=2048)


class AnomalyResolve(BaseModel):
    """Body for ``POST /anomalies/{id}/resolve`` (CR §4)."""

    actor: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=2048)


class SeverityCount(BaseModel):
    severity: str
    count: int


class TypeCount(BaseModel):
    anomaly_type: str
    count: int


class AnomalySummary(BaseModel):
    """Aggregate view for a device or the whole fleet (CR §4, §6)."""

    device_id: str | None = None
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None
    total: int
    open: int
    acknowledged: int
    investigating: int
    resolved: int
    by_severity: list[SeverityCount]
    by_type: list[TypeCount]
    first_detected_at: datetime | None = None
    last_detected_at: datetime | None = None
    avg_duration_seconds: float | None = None
    unresolved: int


class HourlyBucket(BaseModel):
    """One hour of anomaly counts (CR §4, §6)."""

    hour: datetime
    total: int
    open: int
    resolved: int
    by_severity: dict[str, int]


class AnomalyHourly(BaseModel):
    device_id: str | None = None
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None
    buckets: list[HourlyBucket]


class RecurrenceEntry(BaseModel):
    """A repeated anomaly keyed by device/type/cell (CR §6)."""

    device_id: str
    anomaly_type: str
    affected_cell: int | None = None
    occurrences: int
    first_detected_at: datetime
    last_detected_at: datetime
    max_duration_seconds: float
    unresolved: int


class RecurrenceReport(BaseModel):
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None
    entries: list[RecurrenceEntry]
