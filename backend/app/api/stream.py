"""Live update stream for the dashboard (Phase 8).

An SSE endpoint. The dashboard's ``EventSource`` opens one long-lived request
and receives a small JSON frame whenever telemetry is stored, a discharge cycle
completes, an alert opens/changes/resolves, or a device is registered. The
panels then re-read the REST endpoints they care about.

SSE over WebSockets: the traffic is one-directional (server to browser) and
plain HTTP, so it traverses proxies and needs no extra handshake or client
library. A WebSocket would buy bidirectional chat the dashboard does not use.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.event_bus import EventBus

logger = get_logger(__name__)

router = APIRouter(tags=["stream"])

# How often to emit a keep-alive comment when nothing is happening. Below the
# usual 30-60s idle timeouts of reverse proxies.
HEARTBEAT_S = 15.0


def _bus(request: Request) -> EventBus | None:
    return getattr(request.app.state, "events", None)


@router.get("/stream", summary="Server-sent events for live dashboard updates")
async def stream_events(
    request: Request,
    kinds: str | None = Query(
        None,
        description=(
            "Comma-separated event kinds to receive "
            "(telemetry,cycle,alert,monitor,device). Omit for all."
        ),
    ),
) -> StreamingResponse:
    if not get_settings().stream_enabled:
        return StreamingResponse(
            _one_shot("stream disabled"),
            media_type="text/event-stream",
            status_code=503,
        )
    bus = _bus(request)
    if bus is None:
        # The bus is created in the lifespan; if it is missing the app was
        # not started through it, which the caller cannot fix.
        return StreamingResponse(
            _one_shot("stream unavailable"),
            media_type="text/event-stream",
            status_code=503,
        )

    wanted = (
        {part.strip() for part in kinds.split(",") if part.strip()}
        if kinds
        else None
    )
    subscriber = bus.subscribe()

    async def publisher() -> AsyncIterator[bytes]:
        try:
            yield _comment("connected")
            async for event in bus.stream(subscriber, heartbeat_s=HEARTBEAT_S):
                if await request.is_disconnected():
                    return
                if event is None:
                    yield _comment("keep-alive")
                    continue
                if wanted is not None and event.kind not in wanted:
                    continue
                yield _frame(event.to_frame())
        except asyncio.CancelledError:
            raise
        finally:
            bus.unsubscribe(subscriber)

    return StreamingResponse(
        publisher(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Disable proxy buffering (nginx) so frames arrive promptly.
            "X-Accel-Buffering": "no",
        },
    )


async def _one_shot(message: str) -> AsyncIterator[bytes]:
    yield _comment(message)


def _comment(text: str) -> bytes:
    return f": {text}\n\n".encode()


def _frame(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, default=str)}\n\n".encode()


@router.get("/stream/status", summary="Live stream subscriber count")
async def stream_status(request: Request) -> dict:
    bus = _bus(request)
    if bus is None:
        return {"status": "error", "stream": "not_started"}
    return {
        "status": "ok",
        "stream": "running",
        "subscribers": bus.subscriber_count,
        "published": bus.published,
    }
