"""Persistence for generated report snapshots (CR §8).

Reports are write-once artifacts: :meth:`create` stores a fully rendered
payload and the read paths never recompute it. That is what makes
``GET /reports/{id}`` reproducible — it returns the report as generated, not as
the data looks now.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.db.models import Report


class ReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **fields) -> Report:
        report = Report(**fields)
        self._session.add(report)
        await self._session.flush()
        return report

    async def get(self, report_id: int) -> Report | None:
        result = await self._session.execute(
            select(Report).where(Report.id == report_id)
        )
        return result.scalar_one_or_none()

    async def list_reports(
        self,
        *,
        device_id: str | None = None,
        report_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Report], int]:
        """A filtered page of report metadata plus the unpaged total."""
        filters = []
        if device_id is not None:
            filters.append(Report.device_id == device_id)
        if report_type is not None:
            filters.append(Report.report_type == report_type)

        total = int(
            (
                await self._session.execute(
                    select(func.count()).select_from(Report).where(*filters)
                )
            ).scalar_one()
        )
        rows = await self._session.execute(
            select(Report)
            .where(*filters)
            # The list view renders summaries only; the snapshot
            # payload (a full report JSONB) is deferred so listing
            # a page does not transfer every snapshot in it.
            .options(defer(Report.payload))
            .order_by(Report.created_at.desc(), Report.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(rows.scalars().all()), total
