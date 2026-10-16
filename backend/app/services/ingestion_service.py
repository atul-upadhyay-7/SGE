"""Telemetry ingestion: validate, quarantine, and persist off the hot path (FR-02/04/05).

Design contract:

* **One implementation.** MQTT and the optional HTTP endpoint both call
  :meth:`IngestionService.build_item`, so validation can never diverge.
* **Never block the consumer.** The MQTT path only validates (pure CPU,
  in-memory device cache) and does a non-blocking ``put_nowait``. All
  database work happens in a dedicated writer task that drains a bounded
  queue in batches.
* **No silent loss.** Queue overflow is counted and logged; rejected
  payloads are appended to the quarantine table with a stable reason.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import get_logger
from app.repositories.device_repository import DeviceRepository
from app.repositories.telemetry_repository import InsertOutcome, TelemetryRepository
from app.schemas.telemetry import (
    RejectionRecord,
    TelemetryPayload,
    ValidatedSample,
    adapt_legacy_payload,
    looks_like_legacy,
)
from app.services.device_cache import DeviceCache

logger = get_logger(__name__)

# Stable rejection reasons (machine-readable, safe to alert on).
REASON_UNKNOWN_DEVICE = "unknown_device"
REASON_DEVICE_INACTIVE = "device_inactive"
REASON_DEVICE_ID_MISMATCH = "device_id_mismatch"
REASON_UNSUPPORTED_SCHEMA = "unsupported_schema_version"
REASON_MISSING_FIELD = "missing_field"
REASON_INVALID_TIMESTAMP = "invalid_timestamp"
REASON_NON_FINITE = "non_finite_value"
REASON_INVALID_PAYLOAD = "invalid_payload"
REASON_CELL_COUNT_MISMATCH = "cell_count_mismatch"
REASON_VALUE_OUT_OF_RANGE = "value_out_of_range"
REASON_MALFORMED_LEGACY = "malformed_legacy_payload"
REASON_MALFORMED_PAYLOAD = "malformed_payload"
REASON_CONFLICTING_DUPLICATE = "conflicting_duplicate"
REASON_QUEUE_OVERFLOW = "queue_overflow"


@dataclass(slots=True)
class IngestOutcome:
    """Result of a synchronous (HTTP) ingestion call."""

    status: str  # "stored" | "duplicate" | "rejected"
    message_id: str | None = None
    reason: str | None = None
    detail: str | None = None


class IngestionService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cache: DeviceCache,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._cache = cache
        self._settings = settings
        self._queue: asyncio.Queue[ValidatedSample | RejectionRecord] = asyncio.Queue(
            maxsize=settings.ingest_queue_max
        )
        self._writer_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

        self.stored = 0
        self.duplicates = 0
        self.rejected = 0
        self.dropped = 0
        self.write_errors = 0

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self._stop.clear()
        self._writer_task = asyncio.create_task(
            self._writer_loop(), name="ingestion-writer"
        )
        logger.info(
            "Ingestion writer started (batch=%d, flush=%.2fs)",
            self._settings.ingest_batch_size,
            self._settings.ingest_flush_interval_s,
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._writer_task is not None:
            try:
                await asyncio.wait_for(self._writer_task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._writer_task.cancel()
            self._writer_task = None
        logger.info("Ingestion writer stopped: %s", self.stats())

    # -- validation (shared by both transports) ----------------------------

    def build_item(
        self,
        device_id: str,
        raw: dict[str, Any],
        *,
        source: str = "mqtt",
        topic: str | None = None,
    ) -> ValidatedSample | RejectionRecord:
        """Validate a decoded payload; never raises."""
        original = raw
        if looks_like_legacy(raw):
            try:
                raw = adapt_legacy_payload(raw, device_id)
            except Exception as exc:  # noqa: BLE001 - malformed legacy frame
                return self._reject(
                    REASON_MALFORMED_LEGACY,
                    str(exc),
                    device_id,
                    None,
                    topic,
                    source,
                    original,
                )

        try:
            payload = TelemetryPayload.model_validate(raw)
        except ValidationError as exc:
            reason = _classify_validation_error(exc)
            return self._reject(
                reason,
                _summarise(exc),
                device_id,
                _maybe(raw.get("message_id")),
                topic,
                source,
                original,
            )

        if payload.device_id != device_id:
            return self._reject(
                REASON_DEVICE_ID_MISMATCH,
                f"payload device_id '{payload.device_id}' != topic/path '{device_id}'",
                device_id,
                payload.message_id,
                topic,
                source,
                original,
            )

        if payload.schema_version > self._settings.max_schema_version:
            return self._reject(
                REASON_UNSUPPORTED_SCHEMA,
                f"schema_version {payload.schema_version} unsupported",
                device_id,
                payload.message_id,
                topic,
                source,
                original,
            )

        device = self._cache.get(device_id)
        if device is None:
            return self._reject(
                REASON_UNKNOWN_DEVICE,
                "device is not registered",
                device_id,
                payload.message_id,
                topic,
                source,
                original,
            )
        if device.status != "active":
            return self._reject(
                REASON_DEVICE_INACTIVE,
                f"device status is '{device.status}'",
                device_id,
                payload.message_id,
                topic,
                source,
                original,
            )

        if len(payload.cell_voltages) != device.cell_count:
            return self._reject(
                REASON_CELL_COUNT_MISMATCH,
                f"got {len(payload.cell_voltages)} cells, device declares {device.cell_count}",
                device_id,
                payload.message_id,
                topic,
                source,
                original,
            )

        range_reason = self._check_ranges(payload)
        if range_reason is not None:
            return self._reject(
                REASON_VALUE_OUT_OF_RANGE,
                range_reason,
                device_id,
                payload.message_id,
                topic,
                source,
                original,
            )

        return ValidatedSample(
            device_id=payload.device_id,
            message_id=payload.message_id,
            measured_at=payload.measured_at,
            current=payload.current,
            temperature=payload.temperature,
            cell_voltages=payload.cell_voltages,
            pack_voltage=payload.pack_voltage,
            additional_data=payload.extras or None,
            quality_flags=self._quality_flags(payload),
        )

    def _check_ranges(self, payload: TelemetryPayload) -> str | None:
        s = self._settings
        for index, cell_v in enumerate(payload.cell_voltages):
            if not (self._settings.cell_voltage_min_v <= cell_v <= self._settings.cell_voltage_max_v):
                return (
                    f"cell[{index}]={cell_v}V outside "
                    f"[{s.cell_voltage_min_v}, {s.cell_voltage_max_v}]"
                )
        if payload.pack_voltage is not None and not (
            self._settings.pack_voltage_min_v
            <= payload.pack_voltage
            <= s.pack_voltage_max_v
        ):
            return f"pack_voltage {payload.pack_voltage} outside [{s.pack_voltage_min_v}, {s.pack_voltage_max_v}]"
        if not (self._settings.current_min_a <= payload.current <= s.current_max_a):
            return f"current {payload.current} outside [{s.current_min_a}, {s.current_max_a}]"
        if not (
            self._settings.temperature_min_c
            <= payload.temperature
            <= s.temperature_max_c
        ):
            return f"temperature {payload.temperature} outside [{s.temperature_min_c}, {s.temperature_max_c}]"
        return None

    def _quality_flags(self, payload: TelemetryPayload) -> dict[str, Any]:
        spread = max(payload.cell_voltages) - min(payload.cell_voltages)
        flags: dict[str, Any] = {"cell_imbalance_v": round(spread, 4)}
        if spread > self._settings.cell_imbalance_warn_v:
            flags["cell_imbalance_high"] = True
        return flags

    def _reject(
        self,
        reason: str,
        detail: str | None,
        device_id: str | None,
        message_id: str | None,
        topic: str | None,
        source: str,
        raw: dict[str, Any] | None,
    ) -> RejectionRecord:
        return RejectionRecord(
            reason=reason,
            detail=detail,
            device_id=device_id,
            message_id=message_id,
            topic=topic,
            source=source,
            raw_payload=raw,
        )

    # -- transports --------------------------------------------------------

    async def ingest(
        self, device_id: str, raw: dict[str, Any], *, topic: str | None = None
    ) -> None:
        """MQTT path: validate and hand off without touching the database."""
        item = self.build_item(device_id, raw, source="mqtt", topic=topic)
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self.dropped += 1
            logger.error(
                "Ingestion queue full (max=%d); dropped sample device=%s",
                self._settings.ingest_queue_max,
                device_id,
            )

    async def reject_malformed(
        self, device_id: str, topic: str | None, detail: str
    ) -> None:
        """Quarantine a payload that could not even be decoded as JSON."""
        item = self._reject(
            REASON_MALFORMED_PAYLOAD, detail, device_id, None, topic, "mqtt", None
        )
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self.dropped += 1
            logger.error("Ingestion queue full; dropped malformed payload record")

    async def ingest_now(
        self, device_id: str, raw: dict[str, Any], *, source: str = "http"
    ) -> IngestOutcome:
        """HTTP path: validate and persist synchronously so the caller gets a verdict."""
        item = self.build_item(device_id, raw, source=source)
        if isinstance(item, RejectionRecord):
            await self._flush([item])
            return IngestOutcome(
                status="rejected", reason=item.reason, detail=item.detail
            )

        outcome = await self._flush([item])
        if outcome.duplicates:
            if item in outcome.conflicting:
                return IngestOutcome(
                    status="rejected",
                    message_id=item.message_id,
                    reason=REASON_CONFLICTING_DUPLICATE,
                    detail="message_id already stored with a different payload",
                )
            return IngestOutcome(status="duplicate", message_id=item.message_id)
        return IngestOutcome(status="stored", message_id=item.message_id)

    # -- writer ------------------------------------------------------------

    async def _writer_loop(self) -> None:
        batch_size = self._settings.ingest_batch_size
        interval = self._settings.ingest_flush_interval_s
        while not self._stop.is_set():
            try:
                first = await asyncio.wait_for(self._queue.get(), timeout=interval)
            except asyncio.TimeoutError:
                continue
            batch = [first]
            while len(batch) < batch_size:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            await self._flush(batch)
        await self.flush_pending()

    async def flush_pending(self) -> None:
        """Drain whatever is queued right now (used on shutdown and in tests)."""
        items: list[ValidatedSample | RejectionRecord] = []
        while True:
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if items:
            await self._flush(items)

    async def _flush(
        self, items: Iterable[ValidatedSample | RejectionRecord]
    ) -> InsertOutcome:
        items = list(items)
        samples = [i for i in items if isinstance(i, ValidatedSample)]
        rejections = [i for i in items if isinstance(i, RejectionRecord)]
        outcome = InsertOutcome()

        if not items:
            return outcome

        async with self._session_factory() as session:
            try:
                if samples:
                    telemetry = TelemetryRepository(session)
                    outcome = await telemetry.insert_batch(samples)
                    self.stored += outcome.inserted
                    self.duplicates += outcome.duplicates
                    await self._touch_devices(session, samples)
                    for sample in outcome.conflicting:
                        rejections.append(
                            RejectionRecord(
                                reason=REASON_CONFLICTING_DUPLICATE,
                                detail="message_id reused with a different payload",
                                device_id=sample.device_id,
                                message_id=sample.message_id,
                                source="mqtt",
                            )
                        )
                if rejections:
                    await TelemetryRepository(session).insert_rejections(rejections)
                    self.rejected += len(rejections)
                await session.commit()
            except Exception:  # noqa: BLE001 - a bad batch must not kill the writer
                await session.rollback()
                self.write_errors += 1
                logger.exception(
                    "Ingestion flush failed for %d item(s); batch dropped", len(items)
                )
        return outcome

    async def _touch_devices(
        self, session: AsyncSession, samples: list[ValidatedSample]
    ) -> None:
        latest: dict[str, datetime] = {}
        for sample in samples:
            current = latest.get(sample.device_id)
            if current is None or sample.measured_at > current:
                latest[sample.device_id] = sample.measured_at
        repository = DeviceRepository(session)
        for device_id, seen_at in latest.items():
            await repository.touch_last_seen(device_id, seen_at)

    # -- observability -----------------------------------------------------

    def stats(self) -> dict[str, int]:
        return {
            "queued": self._queue.qsize(),
            "stored": self.stored,
            "duplicates": self.duplicates,
            "rejected": self.rejected,
            "dropped": self.dropped,
            "write_errors": self.write_errors,
        }


def _classify_validation_error(exc: ValidationError) -> str:
    errors = exc.errors()
    for error in errors:
        error_type = error.get("type", "")
        location = ".".join(str(part) for part in error.get("loc", ()))
        message = error.get("msg", "")
        if "measured_at" in location:
            return REASON_INVALID_TIMESTAMP
        if error_type == "missing":
            return REASON_MISSING_FIELD
        if "finite" in message.lower() or "inf" in message.lower():
            return REASON_NON_FINITE
    return REASON_INVALID_PAYLOAD


def _summarise(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location or '<root>'}: {first.get('msg', 'invalid')}"


def _maybe(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:128] or None
