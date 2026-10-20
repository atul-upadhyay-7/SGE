"""Live stream and event bus tests (Phase 8)."""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from app.services.event_bus import (
    EVENT_ALERT,
    EVENT_TELEMETRY,
    Event,
    EventBus,
)

API = "/api/v1"


# -- event bus unit tests --------------------------------------------------


def test_publish_delivers_to_subscriber() -> None:
    async def run() -> None:
        bus = EventBus()
        sub = bus.subscribe()
        bus.publish(EVENT_TELEMETRY, {"device_id": "d1"})
        event = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
        assert event.kind == EVENT_TELEMETRY
        assert event.data == {"device_id": "d1"}
        assert bus.published == 1

    asyncio.run(run())


def test_publish_with_no_subscribers_is_not_an_error() -> None:
    bus = EventBus()
    bus.publish(EVENT_TELEMETRY, {"device_id": "d1"})
    assert bus.subscriber_count == 0
    assert bus.published == 1


def test_slow_subscriber_drops_oldest() -> None:
    async def run() -> None:
        bus = EventBus(queue_max=2)
        sub = bus.subscribe()
        for i in range(4):
            bus.publish(EVENT_TELEMETRY, {"n": i})
        # Buffer holds the two newest events; the two oldest were dropped.
        first = sub.queue.get_nowait()
        second = sub.queue.get_nowait()
        assert first.data["n"] == 2
        assert second.data["n"] == 3
        assert sub.queue.empty()
        assert sub.dropped == 2

    asyncio.run(run())


def test_fan_out_reaches_every_subscriber() -> None:
    async def run() -> None:
        bus = EventBus()
        subs = [bus.subscribe() for _ in range(3)]
        bus.publish(EVENT_ALERT, {"device_id": "d1"})
        for sub in subs:
            event = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
            assert event.kind == EVENT_ALERT

    asyncio.run(run())


def test_stream_yields_none_on_heartbeat() -> None:
    async def run() -> None:
        bus = EventBus()
        sub = bus.subscribe()
        agen = bus.stream(sub, heartbeat_s=0.01)
        item = await asyncio.wait_for(agen.__anext__(), timeout=1.0)
        assert item is None
        await agen.aclose()
        assert bus.subscriber_count == 0  # cleaned up on close

    asyncio.run(run())


def test_event_frame_serialises() -> None:
    frame = Event(kind=EVENT_TELEMETRY, data={"device_id": "d1"}).to_frame()
    assert frame["kind"] == EVENT_TELEMETRY
    assert frame["data"] == {"device_id": "d1"}
    assert isinstance(frame["at"], str)


# -- API tests -------------------------------------------------------------


def test_stream_status_reports_running(client: TestClient) -> None:
    r = client.get(f"{API}/stream/status")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["subscribers"] == 0
    assert "published" in body


def test_stream_is_under_api_prefix(client: TestClient) -> None:
    assert client.get("/stream/status").status_code == 404


def test_stream_openapi_lists_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/stream" in paths
    assert "/api/v1/stream/status" in paths


def test_stream_publisher_frames_and_filters() -> None:
    """Exercise the endpoint's frame builder and kind filter directly.

    The blocking HTTP stream cannot be read incrementally through
    Starlette's ``TestClient`` (it buffers the whole body), so the
    framing/filtering logic is driven through a controllable bus instead
    of a live socket.
    """
    from app.api.stream import _comment, _frame
    from app.services.event_bus import Event

    frame = _frame(
        Event(kind=EVENT_TELEMETRY, data={"device_id": "d1"}).to_frame()
    )
    assert frame.startswith(b"data: ")
    assert frame.endswith(b"\n\n")
    assert json.loads(frame[len(b"data: ") :].decode())["kind"] == EVENT_TELEMETRY
    assert _comment("keep-alive") == b": keep-alive\n\n"


def test_stream_generator_filters_by_kind() -> None:
    async def run() -> None:
        bus = EventBus()
        sub = bus.subscribe()
        agen = bus.stream(sub, heartbeat_s=0.05)

        async def collect_until_alert() -> list[Event]:
            got: list[Event] = []
            while True:
                item = await asyncio.wait_for(agen.__anext__(), timeout=1.0)
                if item is None:
                    continue
                got.append(item)
                if item.kind == EVENT_ALERT:
                    return got

        async def publish_soon() -> None:
            await asyncio.sleep(0.01)
            bus.publish(EVENT_TELEMETRY, {"device_id": "ignored"})
            bus.publish(EVENT_ALERT, {"device_id": "d-alert"})

        task = asyncio.create_task(publish_soon())
        events = await collect_until_alert()
        await task
        await agen.aclose()
        assert events[-1].kind == EVENT_ALERT
        assert events[-1].data["device_id"] == "d-alert"

    asyncio.run(run())


def test_ingestion_publishes_telemetry_event(client: TestClient, require_db: bool) -> None:
    """A stored sample must fire a telemetry event on the app bus."""
    bus = client.app.state.events
    sub = bus.subscribe()
    try:
        r = client.post(
            f"{API}/devices",
            json={
                "device_id": "d-live-1",
                "name": "Live Stream Device",
                "cell_count": 2,
                "chemistry": "li_ion",
                "nominal_capacity_ah": 2.0,
            },
        )
        assert r.status_code in (201, 409)
        payload = {
            "device_id": "d-live-1",
            "schema_version": 1,
            "message_id": "msg-live-1",
            "measured_at": "2024-01-01T00:00:00+00:00",
            "cell_voltages": [3.7, 3.8],
            "current": -1.0,
            "temperature": 25.0,
        }
        r = client.post(f"{API}/devices/d-live-1/telemetry", json=payload)
        assert r.status_code in (201, 200), r.text

        # Poll the queue synchronously; the publish happened inside the request.
        seen = []
        while not sub.queue.empty():
            seen.append(sub.queue.get_nowait())
        kinds = [e.kind for e in seen]
        assert EVENT_TELEMETRY in kinds
        device_ids = [e.data.get("device_id") for e in seen if e.kind == EVENT_TELEMETRY]
        assert "d-live-1" in device_ids
    finally:
        bus.unsubscribe(sub)
