"""Anomaly endpoints (CR §3, §4, §6).

Reads surface what the detector persisted (Stage 4) and the acknowledgement /
action / resolve endpoints append to an anomaly's audit trail. Anomalies are
never created here: an episode is only ever opened by the versioned detector
reconciling a real detection, so the API cannot mint an anomaly by hand.

Route order matters: the literal ``/anomalies/summary``, ``/hourly`` and
``/recurrence`` paths are declared before ``/anomalies/{anomaly_id}`` so the
path parameter never captures them.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.repositories.anomaly_repository import AnomalyRepository
from app.schemas.anomaly import (
    AnomalyAcknowledge,
    AnomalyActionCreate,
    AnomalyActionRead,
    AnomalyEventRead,
    AnomalyHourly,
    AnomalyPage,
    AnomalyResolve,
    AnomalySummary,
    HourlyBucket,
    RecurrenceReport,
    SeverityCount,
    TypeCount,
)
from app.services.event_bus import EVENT_ALERT

router = APIRouter(tags=["anomalies"])

UTC = timezone.utc
_MAX_PAGE = 1000


@router.get("/anomalies", response_model=AnomalyPage)
async def list_anomalies(
    device_id: str | None = Query(None),
    severity: str | None = Query(None),
    anomaly_type: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    affected_cell: int | None = Query(None, ge=0),
    min_recurrence: int = Query(1, ge=1),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=_MAX_PAGE),
    session: AsyncSession = Depends(get_session),
) -> AnomalyPage:
    """List anomalies, newest first, with optional filters."""
    start, end = _range(from_, to)
    items, total = await AnomalyRepository(session).list_events(
        device_id=device_id,
        severity=severity,
        anomaly_type=anomaly_type,
        status=status_filter,
        affected_cell=affected_cell,
        min_recurrence=min_recurrence,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )
    return AnomalyPage(
        device_id=device_id,
        count=len(items),
        limit=limit,
        offset=offset,
        total=total,
        items=[AnomalyEventRead.model_validate(item) for item in items],
    )


@router.get("/anomalies/summary", response_model=AnomalySummary)
async def anomaly_summary(
    device_id: str | None = Query(None),
    severity: str | None = Query(None),
    anomaly_type: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    affected_cell: int | None = Query(None, ge=0, description="0 = pack-level; positive = 1-based cell"),
    min_recurrence: int = Query(1, ge=1),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> AnomalySummary:
    """Aggregate anomaly counts for a device or the whole fleet."""
    start, end = _range(from_, to)
    rows = await AnomalyRepository(session).summary_rows(
        device_id=device_id, start=start, end=end, severity=severity, anomaly_type=anomaly_type, status=status_filter, affected_cell=affected_cell, min_recurrence=min_recurrence
    )
    status_counts: dict[str, int] = rows["status_counts"]
    return AnomalySummary(
        device_id=device_id,
        **{"from": start, "to": end},
        total=rows["total"],
        open=status_counts.get("open", 0),
        acknowledged=status_counts.get("acknowledged", 0),
        investigating=status_counts.get("investigating", 0),
        resolved=status_counts.get("resolved", 0),
        false_positive=status_counts.get("false_positive", 0),
        by_severity=[
            SeverityCount(severity=name, count=count)
            for name, count in rows["severity_counts"]
        ],
        by_type=[
            TypeCount(anomaly_type=name, count=count)
            for name, count in rows["type_counts"]
        ],
        first_detected_at=rows["first_detected_at"],
        last_detected_at=rows["last_detected_at"],
        avg_duration_seconds=rows["avg_duration_seconds"],
        unresolved=rows["total"] - status_counts.get("resolved", 0) - status_counts.get("false_positive", 0),
    )


@router.get("/anomalies/hourly", response_model=AnomalyHourly)
async def anomaly_hourly(
    device_id: str | None = Query(None),
    severity: str | None = Query(None),
    anomaly_type: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    affected_cell: int | None = Query(None, ge=0, description="0 = pack-level; positive = 1-based cell"),
    min_recurrence: int = Query(1, ge=1),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> AnomalyHourly:
    """Anomaly counts per hour, with a per-hour severity breakdown."""
    start, end = _range(from_, to)
    rows = await AnomalyRepository(session).hourly_activity(
        device_id=device_id, start=start, end=end, severity=severity, anomaly_type=anomaly_type, status=status_filter, affected_cell=affected_cell, min_recurrence=min_recurrence
    )
    return AnomalyHourly(
        device_id=device_id,
        **{"from": start, "to": end},
        buckets=[
            HourlyBucket(
                hour=row["hour"],
                total=row["total"],
                open=row["open"],
                resolved=row["resolved"],
                false_positive=row.get("false_positive", 0),
                by_severity=row["by_severity"],
            )
            for row in rows
        ],
    )


@router.get("/anomalies/recurrence", response_model=RecurrenceReport)
async def anomaly_recurrence(
    device_id: str | None = Query(None),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    limit: int = Query(100, ge=1, le=_MAX_PAGE),
    session: AsyncSession = Depends(get_session),
) -> RecurrenceReport:
    """Repeated anomalies grouped by device/type/cell (CR §6)."""
    start, end = _range(from_, to)
    entries = await AnomalyRepository(session).recurrence(
        device_id=device_id, start=start, end=end, limit=limit
    )
    return RecurrenceReport(**{"from": start, "to": end}, entries=entries)


@router.get("/anomalies/comparison")
async def anomaly_comparison(
    from_: datetime = Query(..., alias="from"),
    to: datetime = Query(...),
    device_id: str | None = Query(None),
    severity: str | None = Query(None),
    anomaly_type: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    affected_cell: int | None = Query(None, ge=0),
    min_recurrence: int = Query(1, ge=1),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Same-length adjacent periods; no overlapping boundary episodes."""
    start, end = _range(from_, to)
    if start == end:
        raise HTTPException(status_code=400, detail="comparison requires a positive-duration period")
    previous_start = start - (end - start)
    previous_end = start - timedelta(microseconds=1)
    repo = AnomalyRepository(session)
    filters = dict(device_id=device_id, severity=severity, anomaly_type=anomaly_type, status=status_filter, affected_cell=affected_cell, min_recurrence=min_recurrence)
    current = await repo.summary_rows(start=start, end=end, **filters)
    previous = await repo.summary_rows(start=previous_start, end=previous_end, **filters)
    def metrics(row):
        return {"total": row["total"], "unresolved": row["total"] - row["status_counts"].get("resolved", 0) - row["status_counts"].get("false_positive", 0), "by_type": dict(row["type_counts"]), "by_severity": dict(row["severity_counts"])}
    a, b = metrics(current), metrics(previous)
    return {"current": {"from": start.isoformat(), "to": end.isoformat(), **a}, "previous": {"from": previous_start.isoformat(), "to": previous_end.isoformat(), **b}, "total_change": a["total"] - b["total"], "total_change_percent": ((a["total"] - b["total"]) / b["total"] * 100) if b["total"] else None, "note": "Distinct episodes detected in each period. Current persisted status, not historical status at period end. Recurrence evaluated independently in each period."}


@router.get("/anomalies/{anomaly_id}", response_model=AnomalyEventRead)
async def get_anomaly(
    anomaly_id: int,
    session: AsyncSession = Depends(get_session),
) -> AnomalyEventRead:
    event = await AnomalyRepository(session).get(anomaly_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"anomaly {anomaly_id} not found",
        )
    return AnomalyEventRead.model_validate(event)


@router.get(
    "/anomalies/{anomaly_id}/history", response_model=list[AnomalyActionRead]
)
async def anomaly_history(
    anomaly_id: int,
    session: AsyncSession = Depends(get_session),
) -> list[AnomalyActionRead]:
    """The audit trail (acknowledgements, notes, actions, resolution)."""
    repository = AnomalyRepository(session)
    await _require_anomaly(repository, anomaly_id)
    actions = await repository.list_actions(anomaly_id)
    return [AnomalyActionRead.model_validate(action) for action in actions]


@router.post(
    "/anomalies/{anomaly_id}/acknowledge", response_model=AnomalyEventRead
)
async def acknowledge_anomaly(
    anomaly_id: int,
    payload: AnomalyAcknowledge,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AnomalyEventRead:
    """Acknowledge an open anomaly so it stops paging, without resolving it."""
    repository = AnomalyRepository(session)
    event = await _require_anomaly(repository, anomaly_id)
    if event.status in {"resolved", "false_positive"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="cannot acknowledge a resolved anomaly",
        )
    await repository.add_action(
        anomaly_id,
        "acknowledge",
        actor=payload.actor,
        note=payload.note,
    )
    if event.status == "open":
        await repository.set_status(anomaly_id, "acknowledged")
    await session.commit()
    await session.refresh(event)
    _publish(request, event)
    return AnomalyEventRead.model_validate(event)


@router.post("/anomalies/{anomaly_id}/actions", response_model=AnomalyActionRead)
async def add_anomaly_action(
    anomaly_id: int,
    payload: AnomalyActionCreate,
    session: AsyncSession = Depends(get_session),
) -> AnomalyActionRead:
    """Append an entry to an anomaly's audit trail."""
    repository = AnomalyRepository(session)
    event = await _require_anomaly(repository, anomaly_id)
    action_type = payload.action_type
    if action_type == "resolve":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="use POST /anomalies/{id}/resolve to resolve an anomaly",
        )
    action = await repository.add_action(
        anomaly_id,
        action_type,
        actor=payload.actor,
        note=payload.note,
        evidence=payload.evidence,
    )
    if action_type == "corrective_action" and event.status in {
        "open",
        "acknowledged",
    }:
        await repository.set_status(anomaly_id, "investigating")
    await session.commit()
    return AnomalyActionRead.model_validate(action)


@router.post("/anomalies/{anomaly_id}/resolve", response_model=AnomalyEventRead)
async def resolve_anomaly(
    anomaly_id: int,
    payload: AnomalyResolve,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AnomalyEventRead:
    """Resolve an anomaly manually, ending its episode."""
    repository = AnomalyRepository(session)
    event = await _require_anomaly(repository, anomaly_id)
    if event.status in {"resolved", "false_positive"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="anomaly is already resolved",
        )
    if payload.classification == "false_positive" and not (payload.outcome or payload.note or "").strip():
        raise HTTPException(status_code=422, detail="A false-positive classification requires an explanation")
    now = datetime.now(UTC)
    await repository.add_action(
        anomaly_id,
        "resolve",
        actor=payload.actor,
        note=payload.note,
        evidence={"classification": payload.classification, "outcome": payload.outcome or payload.note},
    )
    event.status = payload.classification
    event.resolution_outcome = payload.outcome or payload.note
    event.ended_at = now
    span = (now - event.detected_at).total_seconds()
    event.duration_seconds = max(span, event.duration_seconds)
    await session.commit()
    await session.refresh(event)
    _publish(request, event)
    return AnomalyEventRead.model_validate(event)


def _range(
    from_: datetime | None, to: datetime | None
) -> tuple[datetime | None, datetime | None]:
    start = _as_utc(from_)
    end = _as_utc(to)
    if start is not None and end is not None and start > end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'from' must be on or before 'to'",
        )
    return start, end


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _require_anomaly(repository: AnomalyRepository, anomaly_id: int):
    event = await repository.get(anomaly_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"anomaly {anomaly_id} not found",
        )
    return event


def _publish(request: Request, event) -> None:
    events = getattr(request.app.state, "events", None)
    if events is not None:
        events.publish(
            EVENT_ALERT,
            {
                "device_id": event.device_id,
                "anomaly_id": event.id,
                "status": event.status,
                "severity": event.severity,
            },
        )
