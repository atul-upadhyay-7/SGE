"""Cycle history and SoH inference endpoints (FR-06, FR-07).

Aggregation is explicit and idempotent: ``POST .../cycles/aggregate`` replays
the device's stored telemetry through the shared cycle tracker and the partial
window model, then persists cycles and predictions. The GET endpoints read the
persisted results and never run inference.
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
    SohPage,
    SohPredictionRead,
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


@router.get("/soh", response_model=SohPage)
async def list_soh(
    device_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=_MAX_PAGE),
    session: AsyncSession = Depends(get_session),
) -> SohPage:
    await _require_device(session, device_id)
    repository = CycleRepository(session)
    items = await repository.list_predictions(
        device_id, offset=offset, limit=limit
    )
    return SohPage(
        device_id=device_id,
        count=len(items),
        items=[SohPredictionRead.model_validate(item) for item in items],
    )


@router.get("/soh/latest", response_model=SohPredictionRead)
async def latest_soh(
    device_id: str,
    session: AsyncSession = Depends(get_session),
) -> SohPredictionRead:
    await _require_device(session, device_id)
    prediction = await CycleRepository(session).latest_prediction(device_id)
    if prediction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"no SoH prediction for device '{device_id}'"
            ),
        )
    return SohPredictionRead.model_validate(prediction)


async def _require_device(session: AsyncSession, device_id: str) -> Device:
    device = await DeviceRepository(session).get_by_device_id(device_id)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"device '{device_id}' not registered",
        )
    return device
