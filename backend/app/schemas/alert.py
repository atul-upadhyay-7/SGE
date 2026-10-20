"""Read schemas for device alerts (Phase 5)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: str
    rule_code: str
    severity: str
    message: str
    details: dict | None = None
    status: str
    first_seen_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None = None
    acked_at: datetime | None = None
    acked_by: str | None = None


class AlertPage(BaseModel):
    count: int
    total: int
    items: list[AlertRead]


class AlertAckRequest(BaseModel):
    acked_by: str


class AlertEvaluationResult(BaseModel):
    device_id: str
    opened: int
    updated: int
    resolved: int
    firing: list[str]
