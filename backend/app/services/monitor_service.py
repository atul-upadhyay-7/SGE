"""The background monitoring loop (Phase 5).

One asyncio task in the app lifespan, on its own transaction per device, so
a slow or failing device never blocks the others (PRD NFR: Reliability). Each
tick, for every registered device: optionally re-aggregate it if it has gained
a sample since the last aggregation, then evaluate the alert rules.

The loop is deliberately a plain ``asyncio`` task rather than a scheduler
dependency: the work is a fixed period over a small device fleet, and an
extra framework would add a process-lifetime contract we do not need.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.core.config import Settings
from app.core.logging import get_logger
from app.repositories.device_repository import DeviceRepository
from app.services.alert_service import reconcile
from app.services.cycle_service import CycleAggregationService
from app.services.event_bus import EVENT_ALERT, EVENT_CYCLE, EVENT_MONITOR, EventBus

logger = get_logger(__name__)


class MonitorService:
    def __init__(self, session_factory, settings: Settings, *, events: EventBus | None = None) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._events = events
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()
        self.last_run_at: datetime | None = None
        self.last_error: str | None = None
        self.last_tick_failures = 0
        self.runs = 0

    async def start(self) -> None:
        if not self._settings.monitor_enabled:
            logger.info("Monitoring loop disabled (monitor_enabled=false)")
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._run(), name="bms-monitor")
        logger.info(
            "Monitoring loop started (interval=%ss, auto_aggregate=%s)",
            self._settings.monitor_interval_s,
            self._settings.auto_aggregate,
        )

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Monitoring loop stopped")

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad tick must not kill the loop
                logger.exception("Monitoring tick failed")
                self.last_error = "tick failed; see logs"
            try:
                await asyncio.wait_for(
                    self._stopped.wait(),
                    timeout=self._settings.monitor_interval_s,
                )
            except asyncio.TimeoutError:
                continue

    async def tick(self) -> list[str]:
        """Run one pass over every device. Returns the device_ids processed."""
        now = datetime.now(timezone.utc)
        processed: list[str] = []
        async with self._session_factory() as session:
            devices = await DeviceRepository(session).all_devices()
        failures = 0
        for device in devices:
            try:
                await self._process_device(device.device_id, now)
                processed.append(device.device_id)
            except Exception:  # noqa: BLE001 - isolate per-device failures
                failures += 1
                logger.exception("Monitoring failed for device %s", device.device_id)
        self.last_run_at = now
        self.runs += 1
        self.last_tick_failures = failures
        # A tick where some (or all) devices failed is not "healthy":
        # health derives its 503 from last_error, so per-device failures
        # must surface here instead of being logged and forgotten.
        self.last_error = (
            f"{failures} device(s) failed in the last tick; see logs"
            if failures
            else None
        )
        if self._events is not None:
            self._events.publish(
                EVENT_MONITOR,
                {"processed": len(processed), "device_ids": processed},
            )
        logger.info("Monitoring tick processed %d device(s)", len(processed))
        return processed

    async def _process_device(self, device_id: str, now: datetime) -> None:
        async with self._session_factory() as session:
            device = await DeviceRepository(session).get_by_device_id(device_id)
            if device is None:
                return
            if self._settings.auto_aggregate and _needs_aggregation(device):
                result = await CycleAggregationService(session).aggregate_device(
                    device_id
                )
                device.last_aggregated_at = now
                await session.commit()
                logger.debug(
                    "Aggregated %s: %d cycle(s), %d prediction(s)",
                    device_id,
                    result.cycles,
                    result.predictions,
                )
                if self._events is not None and result.cycles:
                    self._events.publish(
                        EVENT_CYCLE,
                        {
                            "device_id": device_id,
                            "cycles": result.cycles,
                            "predictions": result.predictions,
                        },
                    )
            evaluation = await reconcile(session, device, now=now, settings=self._settings)
            await session.commit()
            if self._events is not None and (
                evaluation.opened or evaluation.resolved
            ):
                self._events.publish(
                    EVENT_ALERT,
                    {
                        "device_id": device_id,
                        "opened": evaluation.opened,
                        "updated": evaluation.updated,
                        "resolved": evaluation.resolved,
                        "firing": evaluation.firing,
                    },
                )


def _needs_aggregation(device) -> bool:
    """True when the device has telemetry the last aggregation did not see."""
    if device.last_seen_at is None:
        return False
    if device.last_aggregated_at is None:
        return True
    return device.last_seen_at > device.last_aggregated_at
