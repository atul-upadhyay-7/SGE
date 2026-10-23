"""Historical report endpoints (CR §8, PRD §4.7).

``POST /reports`` renders a report synchronously from persisted data and stores
the snapshot; the read endpoints return that stored snapshot unchanged, so a
report is reproducible even after the underlying telemetry ages out.

The download endpoint content-negotiates: ``format=json`` re-serves the stored
payload, ``format=csv`` projects its primary tabular section (the anomaly event
list) to CSV. Only ``json`` and ``csv`` are supported — PDF is out of scope.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.deps import require_operator_key
from app.db.database import get_session
from app.repositories.device_repository import DeviceRepository
from app.repositories.report_repository import ReportRepository
from app.schemas.report import (
    ReportCreate,
    ReportDetail,
    ReportPage,
    ReportSummary,
)
from app.services.report_service import FORMAT_VERSION, ReportService, csv_rows

router = APIRouter(tags=["reports"])

UTC = timezone.utc
_MAX_PAGE = 1000


@router.post(
    "/reports",
    response_model=ReportDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_operator_key)],
)
async def create_report(
    request: ReportCreate,
    http_request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> ReportDetail:
    """Generate and persist a report (synchronous, PRD §4.7).

    A request naming a device that is not registered is a 404 rather than an
    empty report, so a typo never looks like "this device has no data".
    """
    device = None
    if request.device_id is not None:
        device = await DeviceRepository(session).get_by_device_id(request.device_id)
        if device is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"device '{request.device_id}' not registered",
            )

    start, end = _as_utc(request.from_), _as_utc(request.to)

    if request.queued:
        queue = getattr(http_request.app.state, "report_queue", None)
        # Reserve the slot synchronously, before the first await:
        # checking has_room() here and submitting after the insert
        # let every concurrent request pass the bound at once.
        if queue is None or not queue.try_reserve():
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="report queue is full; try again shortly",
                headers={"Retry-After": "30"},
            )
        try:
            report = await ReportRepository(session).create(
                report_type=request.report_type,
                device_id=request.device_id,
                period_start=start,
                period_end=end,
                status="queued",
                filters=None,
                sections=request.sections.model_dump(),
                payload={"request": request.model_dump(mode="json", by_alias=True)},
                format_version=FORMAT_VERSION,
                table_name="anomaly_events",
                row_count=0,
                generated_by=request.generated_by,
            )
            await session.commit()
            await session.refresh(report)
        except Exception:
            queue.release_reservation()
            raise
        queue.submit_reserved(report.id)
        response.status_code = status.HTTP_202_ACCEPTED
        return ReportDetail.model_validate(report)

    payload = await ReportService(session).build(request, device)
    rows = csv_rows(payload)

    report = await ReportRepository(session).create(
        report_type=request.report_type,
        device_id=request.device_id,
        period_start=start,
        period_end=end,
        status="ready",
        filters=payload["filters"],
        sections=request.sections.model_dump(),
        payload=payload,
        format_version=FORMAT_VERSION,
        table_name="anomaly_events",
        row_count=len(rows),
        generated_by=request.generated_by,
    )
    await session.commit()
    await session.refresh(report)
    return ReportDetail.model_validate(report)


@router.get("/reports", response_model=ReportPage)
async def list_reports(
    device_id: str | None = Query(None),
    report_type: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=_MAX_PAGE),
    session: AsyncSession = Depends(get_session),
) -> ReportPage:
    """List generated reports, newest first (metadata only)."""
    items, total = await ReportRepository(session).list_reports(
        device_id=device_id,
        report_type=report_type,
        limit=limit,
        offset=offset,
    )
    return ReportPage(
        count=len(items),
        limit=limit,
        offset=offset,
        total=total,
        items=[ReportSummary.model_validate(item) for item in items],
    )


@router.get("/reports/{report_id}", response_model=ReportDetail)
async def get_report(
    report_id: int,
    session: AsyncSession = Depends(get_session),
) -> ReportDetail:
    """Fetch a stored report, including its full snapshot."""
    report = await _require_report(session, report_id)
    return ReportDetail.model_validate(report)


@router.get("/reports/{report_id}/download")
async def download_report(
    report_id: int,
    format: str = Query("json", description="'json', 'csv' or 'pdf'"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Download a stored report as JSON or CSV (PRD §4.7 export formats)."""
    report = await _require_report(session, report_id)
    if report.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"report {report_id} is {report.status}, not ready to download",
        )
    fmt = format.lower()
    if fmt == "json":
        return JSONResponse(
            content=report.payload or {},
            headers=_content_disposition(report_id, "json"),
        )
    if fmt == "pdf":
        from app.services.report_pdf import render_report_pdf
        return Response(content=await run_in_threadpool(render_report_pdf, report.payload or {}, report_id), media_type="application/pdf", headers=_content_disposition(report_id, "pdf"))
    if fmt == "csv":
        buffer = io.StringIO()
        rows = csv_rows(report.payload or {})
        columns = list(rows[0].keys()) if rows else _empty_columns()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return Response(
            content=buffer.getvalue(),
            media_type="text/csv",
            headers=_content_disposition(report_id, "csv"),
        )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="format must be 'json', 'csv' or 'pdf'",
    )


def _empty_columns() -> list[str]:
    from app.services.report_service import _ACTION_COLUMNS

    return list(_ACTION_COLUMNS)


def _content_disposition(report_id: int, extension: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="report-{report_id}.{extension}"'}


async def _require_report(session: AsyncSession, report_id: int):
    report = await ReportRepository(session).get(report_id)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"report {report_id} not found",
        )
    return report


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
