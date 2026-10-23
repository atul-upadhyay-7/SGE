"""MQTT message consumer (FR-02).

The transport in ``client.py`` is unchanged from Phase 1; this module is the
seam where a delivered message becomes either a validated sample queued for
persistence or a quarantined rejection. Nothing here awaits the database.

Topic contract: ``<prefix>/{device_id}/telemetry``. The device identity used
for validation comes from the topic, and any ``device_id`` inside the
payload must agree with it.
"""

from __future__ import annotations

import json

from app.core.logging import get_logger
from app.services.ingestion_service import IngestionService

logger = get_logger(__name__)


def device_id_from_topic(topic: str, prefix: str = "bms") -> str | None:
    """Extract the device id from ``bms/<device_id>/telemetry``."""
    parts = topic.split("/")
    if len(parts) >= 3 and parts[0] == prefix and parts[-1] == "telemetry":
        return parts[-2] or None
    return None


def build_telemetry_handler(
    ingestion: IngestionService, prefix: str = "bms"
):
    async def handle(client, message) -> None:
        topic = str(message.topic)
        device_id = device_id_from_topic(topic, prefix)
        if device_id is None:
            logger.warning("Ignoring telemetry on unexpected topic %s", topic)
            return

        try:
            raw = message.payload
            if isinstance(raw, (bytes, bytearray, memoryview)):
                raw = bytes(raw).decode("utf-8")
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            logger.warning("Non-JSON payload on %s: %s", topic, exc)
            await ingestion.reject_malformed(device_id, topic, str(exc))
            return

        if not isinstance(data, dict):
            await ingestion.reject_malformed(
                device_id, topic, "payload is not a JSON object"
            )
            return

        await ingestion.ingest(device_id, data, topic=topic)

    return handle
