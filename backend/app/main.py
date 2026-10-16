"""Application entry point and lifecycle wiring.

The MQTT task, the ingestion writer task and HTTP request handling all share
one event loop but are decoupled: a slow database write never stalls the
broker consumer (validated samples go through a bounded queue), and a failing
dashboard request can never interrupt ingestion (PRD NFR: Reliability).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import devices, health, telemetry
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.database import SessionLocal, engine
from app.mqtt.client import MqttClientManager
from app.mqtt.consumer import build_telemetry_handler
from app.repositories.device_repository import DeviceRepository
from app.services.device_cache import DeviceCache
from app.services.ingestion_service import IngestionService

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
    logger.info(
        "Starting %s v%s (env=%s)",
        settings.app_name,
        settings.app_version,
        settings.environment,
    )

    cache = DeviceCache()
    ingestion = IngestionService(SessionLocal, cache, settings)
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

    try:
        yield
    finally:
        await manager.stop()
        await ingestion.stop()
        await engine.dispose()
        logger.info("Shutdown complete")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(devices.router, prefix=settings.api_prefix)
app.include_router(telemetry.router, prefix=settings.api_prefix)
