"""Application entry point and lifecycle wiring.

The MQTT task, the ingestion writer task and HTTP request handling all share
one event loop but are decoupled: a slow database write never stalls the
broker consumer (validated samples go through a bounded queue), and a failing
dashboard request can never interrupt ingestion (PRD NFR: Reliability).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from app.api import (
    alerts,
    anomalies,
    cycles,
    devices,
    health,
    models,
    predictions,
    reports,
    rul,
    stream,
    telemetry,
    validation,
)
from app.api.deps import require_read_access
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.database import SessionLocal, engine
from app.mqtt.client import MqttClientManager
from app.mqtt.consumer import build_telemetry_handler
from app.repositories.device_repository import DeviceRepository
from app.services.device_cache import DeviceCache
from app.services.event_bus import EventBus
from app.services.ingestion_service import IngestionService
from app.services.monitor_service import MonitorService
from app.services.report_queue import ReportQueue

settings = get_settings()
logger = get_logger(__name__)


async def _load_device_cache(cache: DeviceCache) -> None:
    try:
        async with SessionLocal() as session:
            devices = await DeviceRepository(session).all_devices()
        cache.load(devices)
        logger.info("Loaded %d device(s) into the ingestion cache", len(devices))
    except Exception:  # noqa: BLE001 - start degraded rather than fail startup
        logger.exception(
            "Could not load the device cache; every payload will be rejected "
            "as unknown_device until a device is registered"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    problems = settings.access_control_problems()
    if problems:
        raise RuntimeError(
            "Refusing to start in production without write protection: "
            + "; ".join(problems)
        )
    logger.info(
        "Starting %s v%s (env=%s)",
        settings.app_name,
        settings.app_version,
        settings.environment,
    )

    cache = DeviceCache()
    events = EventBus(queue_max=settings.stream_queue_max)
    app.state.events = events
    ingestion = IngestionService(SessionLocal, cache, settings, events=events)
    app.state.device_cache = cache
    app.state.ingestion = ingestion

    await _load_device_cache(cache)
    await ingestion.start()

    manager = MqttClientManager(
        settings,
        handler=build_telemetry_handler(ingestion, settings.mqtt_topic_prefix),
    )
    app.state.mqtt = manager
    await manager.start()

    monitor = MonitorService(SessionLocal, settings, events=events)
    app.state.monitor = monitor
    await monitor.start()

    report_queue = ReportQueue(
        SessionLocal,
        max_pending=settings.report_queue_max_pending,
        concurrency=settings.report_worker_concurrency,
        running_stale_seconds=settings.report_running_stale_seconds,
        heartbeat_seconds=settings.report_heartbeat_seconds,
    )
    app.state.report_queue = report_queue
    await report_queue.start()

    try:
        yield
    finally:
        await report_queue.stop()
        await monitor.stop()
        await manager.stop()
        await ingestion.stop()
        await engine.dispose()
        logger.info("Shutdown complete")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    dependencies=[Depends(require_read_access)],
    # The interactive docs and schema list every route and are not covered by
    # the read guard, so production does not serve them at all.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

# CORS for a browser frontend on another origin. Credentials are off by
# default because the API authenticates devices with headers, not cookies;
# turning them on with a wildcard origin would be rejected by the browser
# anyway (the CORS spec forbids the pair), so the two are set independently.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins(),
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


class IngestBodySizeLimitMiddleware:
    """Cap telemetry ingestion bodies before FastAPI buffers them.

    Only ``POST .../telemetry`` is bounded (a sample is a handful of
    numbers). The body is read up to the limit and replayed to the
    app; one byte over and the request is a 413 without the payload
    ever reaching validation or the quarantine table. Chunked
    requests without a Content-Length are counted the same way.
    """

    def __init__(self, app, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not scope.get("path", "").endswith("/telemetry")
        ):
            await self.app(scope, receive, send)
            return

        messages = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    response = JSONResponse(
                        status_code=413,
                        content={
                            "detail": (
                                "telemetry body exceeds "
                                f"{self.max_bytes} bytes"
                            )
                        },
                    )
                    await response(scope, receive, send)
                    return
                messages.append(message)
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                break

        async def replay():
            if messages:
                return messages.pop(0)
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay, send)


app.add_middleware(
    IngestBodySizeLimitMiddleware,
    max_bytes=settings.telemetry_max_body_bytes,
)

@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    """Service identity for humans and uptime probes hitting the base URL.

    Without this, ``GET /`` was a bare 404 that read like a broken
    deployment. It sits behind the same global read guard as every
    other route, and points at the versioned API and its health check.
    """
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "api": settings.api_prefix,
        "health": f"{settings.api_prefix}/health",
        "docs": "/docs",
    }


app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(devices.router, prefix=settings.api_prefix)
app.include_router(telemetry.router, prefix=settings.api_prefix)
app.include_router(cycles.router, prefix=settings.api_prefix)
app.include_router(predictions.router, prefix=settings.api_prefix)
app.include_router(models.router, prefix=settings.api_prefix)
app.include_router(rul.router, prefix=settings.api_prefix)
app.include_router(alerts.router, prefix=settings.api_prefix)
app.include_router(anomalies.router, prefix=settings.api_prefix)
app.include_router(reports.router, prefix=settings.api_prefix)
app.include_router(stream.router, prefix=settings.api_prefix)
app.include_router(validation.router, prefix=settings.api_prefix)
