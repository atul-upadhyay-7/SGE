"""Background generation for large reports.

``POST /reports`` can ask for ``queued: true``. The request is stored as a
``queued`` row, a bounded in-process queue feeds a small number of workers, and
the row becomes ``ready`` (or ``failed`` with the error type) when the snapshot
is built. The request never holds a connection while a large snapshot builds.

The queue is deliberately in-process, but the database row is the source of
truth and claiming is atomic (``UPDATE ... WHERE status = 'queued'``), so
several API worker processes sharing one database can never build the same
report twice: the loser of the claim sees the row already ``running`` and
skips it. Restart recovery re-queues ``queued`` rows and returns rows stuck
``running`` for longer than ``running_stale_seconds`` (a crashed process) to
the queue; a healthy build that exceeds that threshold could be claimed twice,
so the threshold must stay well above the slowest expected build. What remains
per-process is the pending bound and work distribution: a strict global bound
or balanced load across replicas would need a shared external queue, which
this does not provide.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Report

logger = get_logger(__name__)

PENDING = ("queued", "running")


class ReportQueue:
    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        *,
        max_pending: int = 20,
        concurrency: int = 1,
        running_stale_seconds: int = 900,
    ) -> None:
        self._session_factory = session_factory
        self._max_pending = max_pending
        self._concurrency = max(1, concurrency)
        self._running_stale_seconds = running_stale_seconds
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._active = 0

    @property
    def pending(self) -> int:
        return self._queue.qsize() + self._active

    def has_room(self) -> bool:
        return self.pending < self._max_pending

    def submit(self, report_id: int) -> None:
        self._queue.put_nowait(report_id)

    async def start(self) -> None:
        stale_before = datetime.now(timezone.utc) - timedelta(
            seconds=self._running_stale_seconds
        )
        async with self._session_factory() as session:
            # Only a row stuck "running" beyond the stale threshold belongs to
            # a crashed process; a fresh one is being built right now, possibly
            # by another worker process, and must be left alone.
            await session.execute(
                update(Report)
                .where(Report.status == "running", Report.created_at < stale_before)
                .values(status="queued")
            )
            ids = (
                await session.execute(
                    select(Report.id).where(Report.status == "queued").order_by(Report.id)
                )
            ).scalars().all()
            await session.commit()
        for report_id in ids:
            self._queue.put_nowait(report_id)
        if ids:
            logger.info("Re-queued %d unfinished report(s)", len(ids))
        self._workers = [
            asyncio.create_task(self._worker(), name=f"report-worker-{i}")
            for i in range(self._concurrency)
        ]

    async def stop(self) -> None:
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

    async def _worker(self) -> None:
        while True:
            report_id = await self._queue.get()
            self._active += 1
            try:
                await self._build(report_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a bad report must not kill the worker
                logger.exception("Report %s worker crashed", report_id)
            finally:
                self._active -= 1
                self._queue.task_done()

    async def _build(self, report_id: int) -> None:
        # Imported here: the API module imports this one for the queue type.
        from app.repositories.device_repository import DeviceRepository
        from app.schemas.report import ReportCreate
        from app.services.report_service import (
            FORMAT_VERSION,
            ReportService,
            csv_rows,
        )

        async with self._session_factory() as session:
            # Atomic claim: only the worker that flips queued -> running owns
            # the build, so duplicate queue entries or a second process's
            # recovery scan can never build the same report twice.
            claimed = (
                await session.execute(
                    update(Report)
                    .where(Report.id == report_id, Report.status == "queued")
                    .values(status="running")
                    .returning(Report.id)
                )
            ).scalar_one_or_none()
            if claimed is None:
                return
            await session.commit()
            report = await session.get(Report, report_id)
            request_data = (report.payload or {}).get("request") or {}
            try:
                request = ReportCreate.model_validate(request_data)
                device = None
                if request.device_id is not None:
                    device = await DeviceRepository(session).get_by_device_id(
                        request.device_id
                    )
                    if device is None:
                        raise LookupError(f"device '{request.device_id}' not registered")
                payload = await ReportService(session).build(request, device)
                rows = csv_rows(payload)
                report.filters = payload["filters"]
                report.payload = payload
                report.format_version = FORMAT_VERSION
                report.table_name = "anomaly_events"
                report.row_count = len(rows)
                report.status = "ready"
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                report = await session.get(Report, report_id)
                report.status = "failed"
                report.payload = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
                logger.warning("Report %s failed: %s", report_id, type(exc).__name__)
            await session.commit()
