"""FastAPI application entrypoint.

Phase 1 wires configuration, the async database engine, the MQTT consumer
task and the health endpoints. The MQTT task runs on the same event loop as
HTTP but is fully decoupled from request handling, satisfying the PRD rule
that a failing dashboard request must never interrupt ingestion.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.database import engine
from app.mqtt.client import MqttClientManager
from app.mqtt.consumer import build_telemetry_handler

settings = get_settings()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level)
    logger.info(
        "Starting %s v%s (env=%s)",
        settings.app_name,
        settings.app_version,
        settings.environment,
    )

    manager = MqttClientManager(settings, handler=build_telemetry_handler())
    app.state.mqtt = manager
    await manager.start()

    try:
        yield
    finally:
        await manager.stop()
        await engine.dispose()
        logger.info("Shutdown complete")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

app.include_router(health.router, prefix=settings.api_prefix)
