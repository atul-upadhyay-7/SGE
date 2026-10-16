"""Health and connectivity endpoints (FR-08).

``/health`` is liveness and always returns 200. The database and MQTT
checks return 503 when their dependency is unreachable, so an operator (or
a future orchestrator) can act on them directly.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.core.config import get_settings
from app.db.database import check_connection

router = APIRouter(tags=["health"])

_settings = get_settings()


@router.get("/health", summary="Application health")
async def health() -> dict:
    return {
        "status": "ok",
        "app": _settings.app_name,
        "version": _settings.app_version,
        "environment": _settings.environment,
    }


@router.get("/health/database", summary="Database connectivity")
async def health_database(response: Response) -> dict:
    ok, detail = await check_connection()
    if not ok:
        response.status_code = 503
        return {"status": "error", "database": "unreachable", "detail": detail}
    return {"status": "ok", "database": "reachable"}


@router.get("/health/mqtt", summary="MQTT connectivity")
async def health_mqtt(request: Request, response: Response) -> dict:
    manager = getattr(request.app.state, "mqtt", None)
    if manager is None:
        response.status_code = 503
        return {"status": "error", "mqtt": "not_started"}

    status = manager.status()
    if status["enabled"] and not status["connected"]:
        response.status_code = 503
        return {"status": "error", "mqtt": "disconnected", **status}
    return {"status": "ok", "mqtt": "disabled" if not status["enabled"] else "connected", **status}
