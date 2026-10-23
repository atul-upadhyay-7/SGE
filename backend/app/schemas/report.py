"""Request/response models for historical reports (CR §8, PRD §4.7).

A report request is a filter set plus a choice of which optional sections to
render. The generated report itself is stored as a JSON snapshot, so its read
model is deliberately loose: the *shape* of ``sections`` depends on the filters,
and pinning a rigid schema here would make adding a section a breaking change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The four optional content blocks the PRD lets a caller include. The core
# report (executive summary, period/device details, data availability) is
# always present; these are the ones that can be switched off.
SectionName = Literal["telemetry", "soh", "anomalies", "actions"]


class ReportSections(BaseModel):
    """Which optional sections to include (PRD §4.7).

    Defaults mirror the frontend's checkboxes: everything on, because a caller
    asking for a report without naming sections wants the complete one.
    """

    telemetry: bool = True
    soh: bool = True
    anomalies: bool = True
    actions: bool = True

    def enabled_names(self) -> list[str]:
        return [name for name in ("telemetry", "soh", "anomalies", "actions") if getattr(self, name)]


class ReportCreate(BaseModel):
    """The body of ``POST /reports``.

    ``device_id`` is optional: omitting it produces a fleet-wide report, which
    is the PRD's "Device" filter left at "all". ``severity`` and
    ``anomaly_type`` narrow the anomaly section only, and are echoed back in
    ``filters`` so the snapshot records exactly how it was scoped.
    """

    device_id: str | None = None
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None
    report_type: Literal["full"] = "full"
    severity: str | None = None
    anomaly_type: str | None = None
    sections: ReportSections = Field(default_factory=ReportSections)
    generated_by: str | None = Field(default=None, max_length=128)
    # Build in the background worker instead of inside the request. The call
    # returns 202 with a ``queued`` report to poll until it is ``ready``.
    queued: bool = False

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def _check_window(self) -> "ReportCreate":
        if self.from_ is not None and self.to is not None:
            from datetime import UTC
            self.from_ = self.from_.replace(tzinfo=UTC) if self.from_.tzinfo is None else self.from_.astimezone(UTC)
            self.to = self.to.replace(tzinfo=UTC) if self.to.tzinfo is None else self.to.astimezone(UTC)
        if self.from_ is not None and self.to is not None and self.from_ > self.to:
            raise ValueError("'from' must be on or before 'to'")
        return self


class ReportSummary(BaseModel):
    """A lightweight listing row — no payload, so it stays cheap to fetch."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    report_type: str
    device_id: str | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    status: str
    format_version: str
    table_name: str | None = None
    row_count: int
    generated_by: str | None = None
    created_at: datetime


class ReportDetail(ReportSummary):
    """The full stored report, including its rendered snapshot."""

    filters: dict | None = None
    sections: dict | None = None
    payload: dict | None = None


class ReportPage(BaseModel):
    count: int
    limit: int
    offset: int
    total: int
    items: list[ReportSummary]
