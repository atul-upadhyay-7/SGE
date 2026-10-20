"""SoH prediction reads (FR-07, CR §7).

Predictions are produced by cycle-level aggregation (``POST
/devices/{id}/cycles/aggregate``) and read here; the cycle model is never run on
individual raw readings. Each row carries the model version, feature-schema
version, prediction time and input-quality status so a score is reproducible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import InvalidCursor, decode_cursor, encode_cursor
from app.db.database import get_session
from app.db.models import Device
from app.repositories.cycle_repository import CycleRepository
from app.repositories.device_repository import DeviceRepository
from app.schemas.cycle_read import (
    SohHistoryPage,
    SohPredictionRead,
)

router = APIRouter(prefix="/predictions", tags=["predictions"])

_MAX_PAGE = 1000


@router.get("/latest/{device_id}", response_model=SohPredictionRead)
async def latest_prediction(
    device_id: str,
    session: AsyncSession = Depends(get_session),
) -> SohPredictionRead:
    await _require_device(session, device_id)
    prediction = await CycleRepository(session).latest_prediction(device_id)
    if prediction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no SoH prediction for device '{device_id}'",
        )
    return SohPredictionRead.model_validate(prediction)


@router.get("/history/{device_id}", response_model=SohHistoryPage)
async def prediction_history(
    device_id: str,
    from_: datetime | None = Query(
        None, alias="from", description="Inclusive lower bound on predicted_at (UTC)."
    ),
    to: datetime | None = Query(None, description="Inclusive upper bound (UTC)."),
    limit: int = Query(100, ge=1, le=_MAX_PAGE),
    order: Literal["asc", "desc"] = Query("asc"),
    cursor: str | None = Query(None, description="Opaque token from a previous page"),
    session: AsyncSession = Depends(get_session),
) -> SohHistoryPage:
    await _require_device(session, device_id)
    start = _as_utc(from_)
    end = _as_utc(to)
    if start is not None and end is not None and start > end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'from' must be on or before 'to'",
        )

    resume = None
    if cursor:
        try:
            resume = decode_cursor(cursor)
        except InvalidCursor as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    rows = await CycleRepository(session).list_predictions(
        device_id,
        start=start,
        end=end,
        descending=order == "desc",
        limit=limit + 1,
        cursor=resume,
    )
    has_next = len(rows) > limit
    page = rows[:limit]
    next_cursor = (
        encode_cursor(page[-1].predicted_at, page[-1].id)
        if has_next and page
        else None
    )
    return SohHistoryPage(
        device_id=device_id,
        order=order,
        limit=limit,
        count=len(page),
        next_cursor=next_cursor,
        items=[SohPredictionRead.model_validate(row) for row in page],
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _require_device(session: AsyncSession, device_id: str) -> Device:
    device = await DeviceRepository(session).get_by_device_id(device_id)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"device '{device_id}' not registered",
        )
    return device
