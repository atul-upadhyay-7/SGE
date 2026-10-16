"""MQTT message consumer (FR-02).

Phase 1 keeps the handler decode-and-count only. Phase 2 routes the decoded
payload through validation and persistence *here*, so the transport in
``client.py`` never needs to change and ingestion stays out of the request
path.
"""

from __future__ import annotations

import json

import aiomqtt

from app.core.logging import get_logger

logger = get_logger(__name__)


async def handle_telemetry_message(
    client: aiomqtt.Client, message: aiomqtt.Message
) -> None:
    """Decode a telemetry message and log it.

    Non-JSON payloads are counted as rejected rather than raised, so a
    malformed publisher cannot tear down the consumer loop.
    """
    topic = str(message.topic)
    raw = message.payload
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Rejected non-JSON telemetry on %s", topic)
        return

    logger.debug("Telemetry on %s: %s", topic, payload)


def build_telemetry_handler() -> callable:
    return handle_telemetry_message
