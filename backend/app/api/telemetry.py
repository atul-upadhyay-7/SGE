"""Telemetry ingestion (FR-02) and historical queries (Phase 3, CR §1).

Ingestion (``POST /devices/{device_id}/telemetry``) reuses
:meth:`IngestionService.ingest_now`, which shares the exact validation and
persistence code with the MQTT path, so HTTP can never become a second,
divergent ingestion implementation.

The read endpoints are device-scoped on the *telemetry* resource, matching the
frontend/Grafana contract:

* ``GET /telemetry/latest/{device_id}``
* ``GET /telemetry/history/{device_id}`` — time filters + keyset pagination
* ``GET /telemetry/summary/{device_id}``

History uses keyset (``cursor``) pagination bounded by ``(measured_at, id)``
rather than OFFSET, so pages stay stable while new samples arrive between
requests. All timestamps are UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import (
    APIRouter,
    Body,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import InvalidCursor, decode_cursor, encode_cursor
from app.db.database import get_session
from app.db.models import Device
from app.repositories.device_repository import DeviceRepository
from app.repositories.telemetry_repository import TelemetryRepository
from app.schemas.telemetry_read import (
    TelemetryPage,
    TelemetrySampleRead,
    TelemetrySummary,
)
from app.services.ingestion_service import REASON_BAD_CREDENTIAL

router = APIRouter(tags=["telemetry"])

_MAX_PAGE = 1000


@router.post(
    "/devices/{device_id}/telemetry", status_code=status.HTTP_201_CREATED
)
async def ingest_telemetry(
    device_id: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
    x_device_credential: str | None = Header(default=None),
) -> JSONResponse:
    """Synchronous ingestion for devices that cannot hold an MQTT session.

    The credential is taken from the ``X-Device-Credential`` header; a
    payload may instead carry it in a top-level ``credential`` field (the
    same convention the MQTT path uses). The ingestion service strips that
    field before validation, so it is never persisted as telemetry.
    """
    ingestion = request.app.state.ingestion
    outcome = await ingestion.ingest_now(
        device_id,
        payload,
        source="http",
        presented_credential=x_device_credential,
    )

    if outcome.status == "rejected":
        # A credential failure is an authentication problem, not a data
        # problem: answer 401 so clients and monitors treat it as such.
        if outcome.reason == REASON_BAD_CREDENTIAL:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"reason": outcome.reason, "detail": outcome.detail},
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"reason": outcome.reason, "detail": outcome.detail},
        )

    body = {
        "status": outcome.status,
        "device_id": device_id,
        "message_id": outcome.message_id,
    }
    if outcome.status == "duplicate":
        return JSONResponse(status_code=status.HTTP_200_OK, content=body)
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=body)


@router.get("/telemetry/latest/{device_id}", response_model=TelemetrySampleRead)
async def latest_telemetry(
    device_id: str,
    session: AsyncSession = Depends(get_session),
) -> TelemetrySampleRead:
    device = await _require_device(session, device_id)
    sample = await TelemetryRepository(session).latest_sample(device.device_id)
    if sample is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no telemetry stored for device '{device.device_id}'",
        )
    return TelemetrySampleRead.model_validate(sample)


@router.get("/telemetry/history/{device_id}", response_model=TelemetryPage)
async def query_telemetry(
    device_id: str,
    from_: datetime | None = Query(
        None, alias="from", description="Inclusive lower bound on measured_at (UTC)."
    ),
    to: datetime | None = Query(None, description="Inclusive upper bound (UTC)."),
    limit: int = Query(100, ge=1, le=_MAX_PAGE),
    order: Literal["asc", "desc"] = Query("desc"),
    cursor: str | None = Query(None, description="Opaque token from a previous page"),
    session: AsyncSession = Depends(get_session),
) -> TelemetryPage:
    device = await _require_device(session, device_id)
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

    rows = await TelemetryRepository(session).list_samples(
        device.device_id,
        start=start,
        end=end,
        descending=order == "desc",
        limit=limit + 1,
        cursor=resume,
    )
    has_next = len(rows) > limit
    page = rows[:limit]
    next_cursor = (
        encode_cursor(page[-1].measured_at, page[-1].id) if has_next and page else None
    )
    return TelemetryPage(
        device_id=device.device_id,
        order=order,
        limit=limit,
        count=len(page),
        next_cursor=next_cursor,
        items=[TelemetrySampleRead.model_validate(row) for row in page],
    )


@router.get("/telemetry/summary/{device_id}", response_model=TelemetrySummary)
async def telemetry_summary(
    device_id: str,
    session: AsyncSession = Depends(get_session),
) -> TelemetrySummary:
    device = await _require_device(session, device_id)
    repository = TelemetryRepository(session)
    first, last = await repository.measured_at_bounds(device.device_id)
    by_reason = await repository.count_rejections_by_reason(device.device_id)
    return TelemetrySummary(
        device_id=device.device_id,
        status=device.status,
        last_seen_at=device.last_seen_at,
        sample_count=await repository.count_samples(device.device_id),
        first_measured_at=first,
        last_measured_at=last,
        rejection_count=sum(by_reason.values()),
        rejections_by_reason=by_reason,
    )


def _as_utc(value: datetime | None) -> datetime | None:
    """Query params may arrive naive; the column is timestamptz, so pin UTC."""
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
