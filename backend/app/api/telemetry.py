"""Optional HTTP telemetry ingestion (FR-02).

This endpoint reuses :meth:`IngestionService.ingest_now`, which shares the
exact validation and persistence code with the MQTT path, so HTTP can never
become a second, divergent ingestion implementation. It is intended for
testing and constrained devices that cannot hold an MQTT session.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request, status
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/devices/{device_id}/telemetry", tags=["telemetry"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def ingest_telemetry(
    device_id: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> JSONResponse:
    ingestion = request.app.state.ingestion
    outcome = await ingestion.ingest_now(device_id, payload, source="http")

    if outcome.status == "rejected":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "status": "rejected",
                "reason": outcome.reason,
                "detail": outcome.detail,
            },
        )

    body = {
        "status": outcome.status,
        "device_id": device_id,
        "message_id": outcome.message_id,
    }
    if outcome.status == "duplicate":
        return JSONResponse(status_code=status.HTTP_200_OK, content=body)
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=body)
