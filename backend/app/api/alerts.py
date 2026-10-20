"""Device alert endpoints (Phase 5).

Alerts are derived state: the monitor loop (or an explicit evaluation call)
decides which rules are firing and the API only reads and acknowledges the
result. There is no endpoint that creates an alert by hand, because a
hand-typed alert cannot be reconciled against the rule that produced it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.database import get_session
from app.db.models import Device
from app.repositories.alert_repository import AlertRepository
from app.repositories.device_repository import DeviceRepository
from app.schemas.alert import (
    AlertAckRequest,
    AlertEvaluationResult,
    AlertPage,
    AlertRead,
)
from app.services.alert_service import reconcile
from app.services.event_bus import EVENT_ALERT

router = APIRouter(tags=["alerts"])

_MAX_PAGE = 1000


@router.get("/alerts", response_model=AlertPage)
async def list_alerts(
    device_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    severity: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=_MAX_PAGE),
    session: AsyncSession = Depends(get_session),
) -> AlertPage:
    """List alerts, newest first, optionally filtered by device/status/severity."""
    repository = AlertRepository(session)
    items = await repository.list_alerts(
        device_id=device_id,
        status_filter=status_filter,
        severity=severity,
        offset=offset,
        limit=limit,
    )
    total = await repository.count_alerts(
        device_id=device_id, status_filter=status_filter, severity=severity
    )
    return AlertPage(
        count=len(items),
        total=total,
        items=[AlertRead.model_validate(item) for item in items],
    )


@router.get("/alerts/{alert_id}", response_model=AlertRead)
async def get_alert(
    alert_id: int,
    session: AsyncSession = Depends(get_session),
) -> AlertRead:
    alert = await AlertRepository(session).get_by_id(alert_id)
    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"alert {alert_id} not found",
        )
    return AlertRead.model_validate(alert)


@router.post("/alerts/{alert_id}/ack", response_model=AlertRead)
async def ack_alert(
    alert_id: int,
    payload: AlertAckRequest,
    session: AsyncSession = Depends(get_session),
) -> AlertRead:
    """Acknowledge an alert so it stops paging, without resolving it."""
    alert = await AlertRepository(session).ack_alert(
        alert_id,
        acked_by=payload.acked_by[:64],
        acked_at=datetime.now(timezone.utc),
    )
    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"alert {alert_id} not found or already resolved",
        )
    await session.commit()
    return AlertRead.model_validate(alert)


@router.post(
    "/devices/{device_id}/alerts/evaluate",
    response_model=AlertEvaluationResult,
)
async def evaluate_device(
    device_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AlertEvaluationResult:
    """Evaluate this device's alert rules now (mirrors ``/cycles/aggregate``)."""
    device = await _require_device(session, device_id)
    result = await reconcile(session, device, settings=settings)
    await session.commit()
    events = getattr(request.app.state, "events", None)
    if events is not None and (result.opened or result.resolved):
        events.publish(
            EVENT_ALERT,
            {
                "device_id": result.device_id,
                "opened": result.opened,
                "updated": result.updated,
                "resolved": result.resolved,
                "firing": result.firing,
            },
        )
    return AlertEvaluationResult(
        device_id=result.device_id,
        opened=result.opened,
        updated=result.updated,
        resolved=result.resolved,
        firing=result.firing,
    )


@router.get("/devices/{device_id}/alerts", response_model=AlertPage)
async def list_device_alerts(
    device_id: str,
    status_filter: str | None = Query(None, alias="status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=_MAX_PAGE),
    session: AsyncSession = Depends(get_session),
) -> AlertPage:
    await _require_device(session, device_id)
    repository = AlertRepository(session)
    items = await repository.list_alerts(
        device_id=device_id,
        status_filter=status_filter,
        offset=offset,
        limit=limit,
    )
    total = await repository.count_alerts(
        device_id=device_id, status_filter=status_filter
    )
    return AlertPage(
        count=len(items),
        total=total,
        items=[AlertRead.model_validate(item) for item in items],
    )


async def _require_device(session: AsyncSession, device_id: str) -> Device:
    device = await DeviceRepository(session).get_by_device_id(device_id)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"device '{device_id}' not registered",
        )
    return device
