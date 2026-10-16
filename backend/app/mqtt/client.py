"""Async MQTT connection manager (FR-02, NFR: Reliability).

Phase 1 establishes the broker connection, reconnection loop and the
telemetry subscription, and tracks connectivity for ``/health/mqtt``.
Message *persistence* is deliberately absent here: the consumer handler
only decodes and counts, so ingestion reliability is added in one place in
Phase 2 without changing this transport.

The manager runs as a single background task on the FastAPI event loop. It
never blocks the event loop: aiomqtt is fully async.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from typing import Awaitable, Callable, Optional

import aiomqtt

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

MessageHandler = Callable[[aiomqtt.Client, aiomqtt.Message], Awaitable[None]]


class MqttClientManager:
    """Owns the broker connection, reconnection and subscription lifecycle."""

    def __init__(self, settings: Settings, handler: Optional[MessageHandler] = None):
        self._settings = settings
        self._handler = handler

        self._task: asyncio.Task[None] | None = None
        self._client: aiomqtt.Client | None = None
        self._stop_event = asyncio.Event()

        self._connected = False
        self._connected_since: datetime | None = None
        self._last_error: str | None = None
        self._reconnects = 0
        self._messages_received = 0

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if not self._settings.mqtt_enabled:
            logger.info("MQTT disabled (BMS_MQTT_ENABLED=false); consumer not started")
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="mqtt-consumer")
        logger.info(
            "MQTT consumer task started",
            extra={"host": self._settings.mqtt_host, "port": self._settings.mqtt_port},
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("MQTT consumer task stopped")

    # -- run loop ----------------------------------------------------------

    async def _run(self) -> None:
        s = self._settings
        delay = s.mqtt_reconnect_min_s

        while not self._stop_event.is_set():
            try:
                async with aiomqtt.Client(
                    hostname=s.mqtt_host,
                    port=s.mqtt_port,
                    username=s.mqtt_username,
                    password=s.mqtt_password,
                    identifier=s.mqtt_client_id,
                    keepalive=s.mqtt_keepalive,
                ) as client:
                    self._client = client
                    self._connected = True
                    self._connected_since = datetime.now(UTC)
                    self._last_error = None
                    delay = s.mqtt_reconnect_min_s

                    await client.subscribe(s.mqtt_telemetry_topic, qos=s.mqtt_qos)
                    logger.info(
                        "MQTT connected; subscribed to %s (qos=%s)",
                        s.mqtt_telemetry_topic,
                        s.mqtt_qos,
                    )

                    async for message in client.messages:
                        self._messages_received += 1
                        await self._dispatch(client, message)
                        if self._stop_event.is_set():
                            break
            except asyncio.CancelledError:
                raise
            except aiomqtt.MqttError as exc:
                self._last_error = str(exc)
                logger.warning("MQTT error: %s", exc)
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                self._last_error = str(exc)
                logger.exception("Unexpected MQTT consumer error")
            finally:
                self._connected = False
                self._client = None
                self._connected_since = None

            if self._stop_event.is_set():
                break

            self._reconnects += 1
            logger.info("MQTT reconnecting in %.1fs", delay)
            await self._sleep_or_stop(delay)
            delay = min(delay * 2, s.mqtt_reconnect_max_s)

    async def _dispatch(
        self, client: aiomqtt.Client, message: aiomqtt.Message
    ) -> None:
        if self._handler is None:
            return
        try:
            await self._handler(client, message)
        except Exception:  # noqa: BLE001 - one bad message must not drop the loop
            logger.exception("MQTT message handler failed for topic %s", message.topic)

    async def _sleep_or_stop(self, seconds: float) -> None:
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)

    # -- introspection -----------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    def status(self) -> dict:
        return {
            "enabled": self._settings.mqtt_enabled,
            "connected": self._connected,
            "host": self._settings.mqtt_host,
            "port": self._settings.mqtt_port,
            "topic": self._settings.mqtt_telemetry_topic,
            "qos": self._settings.mqtt_qos,
            "connected_since": (
                self._connected_since.isoformat() if self._connected_since else None
            ),
            "reconnects": self._reconnects,
            "messages_received": self._messages_received,
            "last_error": self._last_error,
        }
