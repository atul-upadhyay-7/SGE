"""Cycle aggregation and cycle history (FR-06).

Aggregation is explicit and idempotent: ``POST .../cycles/aggregate`` replays
the device's stored telemetry through the shared cycle tracker and the partial
window model, then persists cycles and predictions. The GET endpoint reads the
persisted results and never runs inference.

SoH prediction reads live on the ``/predictions`` resource (CR §7); this router
owns cycles only.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.db.models import Device
from app.repositories.cycle_repository import CycleRepository
from app.repositories.device_repository import DeviceRepository
from app.schemas.cycle_read import (
    AggregationResult,
    CyclePage,
    CycleRead,
)
from app.services.cycle_service import CycleAggregationService

router = APIRouter(prefix="/devices/{device_id}", tags=["cycles"])

_MAX_PAGE = 1000


@router.post("/cycles/aggregate", response_model=AggregationResult)
async def aggregate_cycles(
    device_id: str,
    session: AsyncSession = Depends(get_session),
) -> AggregationResult:
    """Replay stored telemetry into cycles and refresh this device's SoH."""
    await _require_device(session, device_id)
    result = await CycleAggregationService(session).aggregate_device(device_id)
    await session.commit()
    return result


@router.get("/cycles", response_model=CyclePage)
async def list_cycles(
    device_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=_MAX_PAGE),
    session: AsyncSession = Depends(get_session),
) -> CyclePage:
    await _require_device(session, device_id)
    repository = CycleRepository(session)
    items = await repository.list_cycles(device_id, offset=offset, limit=limit)
    return CyclePage(
        device_id=device_id,
        count=len(items),
        items=[CycleRead.model_validate(item) for item in items],
    )


async def _require_device(session: AsyncSession, device_id: str) -> Device:
    device = await DeviceRepository(session).get_by_device_id(device_id)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"device '{device_id}' not registered",
        )
    return device
