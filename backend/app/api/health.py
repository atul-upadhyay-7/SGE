"""Health and connectivity endpoints (FR-08).

``/health`` is liveness and always returns 200. The database and MQTT
checks return 503 when their dependency is unreachable, so an operator (or
a future orchestrator) can act on them directly.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from sqlalchemy import func, select
from app.db.database import SessionLocal
from app.db.models import TelemetryRejection

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


@router.get("/health/ingestion", summary="Ingestion pipeline counters")
async def health_ingestion(request: Request) -> dict:
    service = getattr(request.app.state, "ingestion", None)
    if service is None:
        return {"status": "error", "ingestion": "not_started"}
    payload = {"status": "ok", "ingestion": "running", **service.stats()}
    try:
        async with SessionLocal() as session:
            payload["persisted_invalid_count"] = await session.scalar(
                select(func.count()).select_from(TelemetryRejection)
            )
    except Exception:  # Counters remain available during a database outage.
        payload["persisted_invalid_count"] = None
        payload["invalid_count_status"] = "database_unavailable"
    else:
        payload["invalid_count_status"] = "ok"
    return payload


@router.get("/health/monitor", summary="Monitoring loop status")
async def health_monitor(request: Request, response: Response) -> dict:
    service = getattr(request.app.state, "monitor", None)
    if service is None:
        response.status_code = 503
        return {"status": "error", "monitor": "not_started"}
    enabled = _settings.monitor_enabled
    payload = {
        "status": "ok",
        "monitor": "running" if enabled else "disabled",
        "enabled": enabled,
        "interval_s": _settings.monitor_interval_s,
        "auto_aggregate": _settings.auto_aggregate,
        "runs": service.runs,
        "last_run_at": service.last_run_at,
        "last_error": service.last_error,
    }
    if service.last_error is not None:
        response.status_code = 503
        payload["status"] = "error"
    return payload


@router.get("/health/inference", summary="Observed SOH inference calls")
async def health_inference(response: Response) -> dict:
    from app.ml.estimator import inference_health
    payload = inference_health()
    if payload['status'] == 'error':
        response.status_code = 503
    return payload
