"""Queued report generation: lifecycle, failure, back-pressure, recovery and claiming."""

from __future__ import annotations

import asyncio
import time

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.services.report_queue import ReportQueue
from app.services.report_service import ReportService
from sqlalchemy import text

API = "/api/v1"


def _wait_for(client, report_id: int, wanted: set[str], timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"{API}/reports/{report_id}").json()
        if body["status"] in wanted:
            return body
        time.sleep(0.1)
    raise AssertionError(f"report {report_id} stuck at {body['status']}")


def test_queued_report_is_accepted_then_becomes_ready(client, require_db) -> None:
    response = client.post(f"{API}/reports", json={"queued": True, "generated_by": "queue-test"})
    assert response.status_code == 202
    created = response.json()
    assert created["status"] == "queued"

    ready = _wait_for(client, created["id"], {"ready", "failed"})
    assert ready["status"] == "ready"
    assert ready["format_version"] == "report-v3"
    assert "request" not in ready["payload"]
    assert ready["payload"]["filters"] is not None
    assert client.get(f"{API}/reports/{created['id']}/download?format=json").status_code == 200


def test_queued_report_matches_a_synchronous_one(client, require_db) -> None:
    sync = client.post(f"{API}/reports", json={}).json()
    queued = client.post(f"{API}/reports", json={"queued": True}).json()
    done = _wait_for(client, queued["id"], {"ready"})
    for key in ("telemetry", "soh", "anomalies"):
        assert done["payload"][key] == sync["payload"][key]


def test_unknown_device_is_rejected_before_queueing(client, require_db) -> None:
    response = client.post(f"{API}/reports", json={"queued": True, "device_id": "NO_SUCH"})
    assert response.status_code == 404


def test_failed_build_is_recorded_and_not_downloadable(client, require_db, monkeypatch) -> None:
    async def boom(self, request, device):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(ReportService, "build", boom)
    created = client.post(f"{API}/reports", json={"queued": True}).json()
    failed = _wait_for(client, created["id"], {"failed", "ready"})
    assert failed["status"] == "failed"
    assert failed["payload"]["error"].startswith("RuntimeError")
    download = client.get(f"{API}/reports/{created['id']}/download?format=json")
    assert download.status_code == 409


def test_full_queue_pushes_back_with_429(client, require_db, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "report_queue_max_pending", 0)
    queue = client.app.state.report_queue
    monkeypatch.setattr(queue, "_max_pending", 0)
    response = client.post(f"{API}/reports", json={"queued": True})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"


def test_interrupted_report_is_resumed_on_start(client, require_db) -> None:
    async def scenario() -> int:
        async with SessionLocal() as session:
            # created_at aged past the stale threshold: this row's process died.
            row = (
                await session.execute(
                    text(
                        "INSERT INTO reports (report_type, status, sections, payload, "
                        "format_version, row_count, created_at) VALUES "
                        "('full', 'running', '{}'::jsonb, '{\"request\": {}}'::jsonb, "
                        "'report-v3', 0, now() - interval '1 hour') RETURNING id"
                    )
                )
            ).scalar_one()
            await session.commit()
        queue = ReportQueue(SessionLocal, max_pending=5, concurrency=1)
        await queue.start()
        for _ in range(100):
            async with SessionLocal() as session:
                state = (
                    await session.execute(
                        text("SELECT status FROM reports WHERE id = :i"), {"i": row}
                    )
                ).scalar_one()
            if state == "ready":
                break
            await asyncio.sleep(0.1)
        await queue.stop()
        return row, state

    report_id, state = asyncio.run(scenario())
    assert state == "ready"


def _insert_report(sql_status: str, created: str) -> str:
    return (
        "INSERT INTO reports (report_type, status, sections, payload, "
        "format_version, row_count, created_at) VALUES "
        f"('full', '{sql_status}', '{{}}'::jsonb, '{{\"request\": {{}}}}'::jsonb, "
        f"'report-v3', 0, {created}) RETURNING id"
    )


def test_fresh_running_row_is_not_stolen_on_start(client, require_db) -> None:
    """A row another process is actively building must survive our startup."""

    async def scenario() -> tuple[int, str]:
        async with SessionLocal() as session:
            row = (
                await session.execute(text(_insert_report("running", "now()")))
            ).scalar_one()
            await session.commit()
        queue = ReportQueue(SessionLocal, max_pending=5, concurrency=1)
        await queue.start()
        await asyncio.sleep(0.5)
        async with SessionLocal() as session:
            state = (
                await session.execute(
                    text("SELECT status FROM reports WHERE id = :i"), {"i": row}
                )
            ).scalar_one()
            # Never leave a permanent "running" fixture behind: it would age
            # past the stale threshold and be reclaimed by later tests.
            await session.execute(text("DELETE FROM reports WHERE id = :i"), {"i": row})
            await session.commit()
        await queue.stop()
        return row, state

    _, state = asyncio.run(scenario())
    assert state == "running"


def test_claim_is_atomic_so_a_ready_row_is_never_rebuilt(client, require_db) -> None:
    """A duplicate queue entry or a second process's scan is a no-op."""

    async def scenario() -> tuple[int, str]:
        async with SessionLocal() as session:
            row = (
                await session.execute(text(_insert_report("ready", "now()")))
            ).scalar_one()
            await session.commit()
        queue = ReportQueue(SessionLocal, max_pending=5, concurrency=1)
        await queue._build(row)  # claim must fail: the row is not "queued"
        async with SessionLocal() as session:
            state = (
                await session.execute(
                    text("SELECT status FROM reports WHERE id = :i"), {"i": row}
                )
            ).scalar_one()
        return row, state

    _, state = asyncio.run(scenario())
    assert state == "ready"


def test_stale_heartbeat_is_reclaimed_despite_fresh_creation(client, require_db) -> None:
    """Liveness, not row age, decides: a dead builder's row is re-queued."""

    async def scenario() -> str:
        async with SessionLocal() as session:
            row = (
                await session.execute(
                    text(
                        "INSERT INTO reports (report_type, status, sections, payload, "
                        "format_version, row_count, created_at, heartbeat_at) VALUES "
                        "('full', 'running', '{}'::jsonb, '{\"request\": {}}'::jsonb, "
                        "'report-v3', 0, now(), now() - interval '1 hour') RETURNING id"
                    )
                )
            ).scalar_one()
            await session.commit()
        queue = ReportQueue(
            SessionLocal, max_pending=5, concurrency=1,
            running_stale_seconds=60, heartbeat_seconds=1,
        )
        await queue.start()
        state = "running"
        for _ in range(100):
            async with SessionLocal() as session:
                state = (
                    await session.execute(
                        text("SELECT status FROM reports WHERE id = :i"), {"i": row}
                    )
                ).scalar_one()
            if state == "ready":
                break
            await asyncio.sleep(0.1)
        await queue.stop()
        return state

    assert asyncio.run(scenario()) == "ready"


def test_live_heartbeat_protects_a_long_build(client, require_db, monkeypatch) -> None:
    """A build outliving the stale threshold is not stolen while heartbeating."""
    calls = {"n": 0}
    original = ReportService.build

    async def slow_build(self, request, device):
        calls["n"] += 1
        await asyncio.sleep(3)
        return await original(self, request, device)

    monkeypatch.setattr(ReportService, "build", slow_build)

    async def scenario() -> str:
        async with SessionLocal() as session:
            row = (
                await session.execute(text(_insert_report("queued", "now()")))
            ).scalar_one()
            await session.commit()
        queue = ReportQueue(
            SessionLocal, max_pending=5, concurrency=1,
            running_stale_seconds=1, heartbeat_seconds=0.2,
        )
        await queue.start()
        await asyncio.sleep(1.5)  # build in progress, past the stale threshold
        # A second process boots; its recovery must leave the live build alone.
        other = ReportQueue(
            SessionLocal, max_pending=5, concurrency=1,
            running_stale_seconds=1, heartbeat_seconds=0.2,
        )
        await other.start()
        await other.stop()
        state = "queued"
        for _ in range(100):
            async with SessionLocal() as session:
                state = (
                    await session.execute(
                        text("SELECT status FROM reports WHERE id = :i"), {"i": row}
                    )
                ).scalar_one()
            if state == "ready":
                break
            await asyncio.sleep(0.1)
        await queue.stop()
        return state

    assert asyncio.run(scenario()) == "ready"
    assert calls["n"] == 1
