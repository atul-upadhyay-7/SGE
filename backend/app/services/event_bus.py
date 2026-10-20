"""In-process publish/subscribe bus for live dashboard updates (Phase 8).

The dashboard should not poll every panel every few seconds to notice a new
sample, a completed discharge or a firing alert. Instead the components that
*produce* those facts publish a small event here, and HTTP subscribers (the
SSE endpoint in ``app/api/stream.py``) receive them as they happen.

This is deliberately in-process: there is one backend process, the events are
lossy "something changed" hints rather than a durable log, and a subscriber
that misses one still sees the truth on its next read of the REST endpoints.
Adding a broker would turn a hint into a delivery contract the panels do not
need and that a single-process app cannot keep across restarts anyway.

Back-pressure rule: a slow subscriber must never slow ingestion or the monitor
loop, so each subscriber has a bounded queue and the oldest event is dropped
when it fills. The drop is counted, not silent.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# Event kinds. Kept as constants so publishers and the dashboard agree on the
# spelling without a shared enum module just for this.
EVENT_TELEMETRY = "telemetry"
EVENT_CYCLE = "cycle"
EVENT_ALERT = "alert"
EVENT_MONITOR = "monitor"
EVENT_DEVICE = "device"

# Default per-subscriber buffer. Big enough to absorb a burst of telemetry
# between two panel reads, small enough that a dead subscriber is reclaimed
# by memory pressure rather than held forever.
DEFAULT_QUEUE_MAX = 256


@dataclass(slots=True)
class Event:
    """One live update. ``data`` is the JSON body the SSE frame carries."""

    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_frame(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "at": self.at.isoformat(),
            "data": self.data,
        }


@dataclass(slots=True, eq=False)
class _Subscriber:
    queue: asyncio.Queue[Event]
    dropped: int = 0


class EventBus:
    """Fan-out of events to any number of async subscribers."""

    def __init__(self, *, queue_max: int = DEFAULT_QUEUE_MAX) -> None:
        self._queue_max = queue_max
        self._subscribers: set[_Subscriber] = set()
        self.published = 0

    def subscribe(self) -> _Subscriber:
        sub = _Subscriber(queue=asyncio.Queue(maxsize=self._queue_max))
        self._subscribers.add(sub)
        logger.debug("Live subscriber added (total=%d)", len(self._subscribers))
        return sub

    def unsubscribe(self, sub: _Subscriber) -> None:
        self._subscribers.discard(sub)
        logger.debug("Live subscriber removed (total=%d)", len(self._subscribers))

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, kind: str, data: dict[str, Any] | None = None) -> None:
        """Fan an event out without ever blocking the caller.

        Called from ingestion, the monitor loop and request handlers, all of
        which must not wait on a slow dashboard reader. A full subscriber
        queue drops its oldest event; the subscriber itself is not removed
        here because it is the generator's job to clean up on disconnect.
        """
        if not self._subscribers:
            self.published += 1
            return
        event = Event(kind=kind, data=data or {})
        for sub in list(self._subscribers):
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    sub.queue.get_nowait()  # drop oldest
                    sub.queue.put_nowait(event)
                    sub.dropped += 1
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    sub.dropped += 1
                    logger.warning(
                        "Live subscriber queue full; event %s dropped", kind
                    )
        self.published += 1

    async def stream(
        self, sub: _Subscriber, *, heartbeat_s: float | None = None
    ) -> AsyncIterator[Event | None]:
        """Yield events for ``sub`` until the consumer stops iterating.

        ``None`` is yielded on each heartbeat interval so the transport can
        send a keep-alive comment; without it a quiet stream looks dead to
        proxies and the browser's EventSource may reconnect needlessly.
        """
        try:
            while True:
                try:
                    if heartbeat_s is None:
                        yield await sub.queue.get()
                    else:
                        yield await asyncio.wait_for(
                            sub.queue.get(), timeout=heartbeat_s
                        )
                except asyncio.TimeoutError:
                    yield None
        finally:
            self.unsubscribe(sub)
