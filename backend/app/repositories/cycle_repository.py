"""Persistence for aggregated discharge cycles and their SoH predictions.

Cycle rows are keyed by ``(device_id, cycle_number)`` and upserted, so a
replayed aggregation is idempotent and never mints a second row for the same
physical cycle. Predictions have no natural key, so an aggregation replaces a
device's predictions wholesale (delete + insert) rather than appending a
duplicate history on every run.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BatteryCycle, SohPrediction


class CycleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_cycles(
        self, device_id: str, rows: list[dict]
    ) -> None:
        """Insert or refresh cycle rows for one device in a single statement."""
        if not rows:
            return
        values = [{"device_id": device_id, **row} for row in rows]
        statement = pg_insert(BatteryCycle).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=["device_id", "cycle_number"],
            set_={
                "start_time": statement.excluded.start_time,
                "end_time": statement.excluded.end_time,
                "capacity_ah": statement.excluded.capacity_ah,
                "reference_capacity_ah": statement.excluded.reference_capacity_ah,
                "measured_soh": statement.excluded.measured_soh,
                "feature_schema_version": statement.excluded.feature_schema_version,
            },
        )
        await self._session.execute(statement)

    async def cycle_id_map(self, device_id: str) -> dict[int, int]:
        """``cycle_number -> cycle id`` for one device."""
        result = await self._session.execute(
            select(BatteryCycle.cycle_number, BatteryCycle.id).where(
                BatteryCycle.device_id == device_id
            )
        )
        return {number: cid for number, cid in result.all()}

    async def list_cycles(
        self, device_id: str, *, offset: int = 0, limit: int = 100
    ) -> list[BatteryCycle]:
        result = await self._session.execute(
            select(BatteryCycle)
            .where(BatteryCycle.device_id == device_id)
            .order_by(BatteryCycle.cycle_number.asc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_cycles(self, device_id: str) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(BatteryCycle)
            .where(BatteryCycle.device_id == device_id)
        )
        return int(result.scalar_one())

    async def latest_cycle(self, device_id: str) -> BatteryCycle | None:
        result = await self._session.execute(
            select(BatteryCycle)
            .where(BatteryCycle.device_id == device_id)
            .order_by(BatteryCycle.cycle_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def delete_predictions(self, device_id: str) -> None:
        await self._session.execute(
            delete(SohPrediction).where(SohPrediction.device_id == device_id)
        )

    async def insert_predictions(self, rows: list[dict]) -> None:
        if not rows:
            return
        await self._session.execute(pg_insert(SohPrediction).values(rows))

    async def list_predictions(
        self,
        device_id: str | None,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        descending: bool = False,
        limit: int = 100,
        cursor: tuple[datetime, int] | None = None,
    ) -> list[SohPrediction]:
        """A page of predictions, optionally bounded by time and resume cursor.

        Ordering is by ``(predicted_at, id)``; the cursor is an exclusive bound
        on that pair, so a page never repeats or skips rows while new
        predictions land between requests.
        """
        query = select(SohPrediction)
        if device_id is not None:
            query = query.where(SohPrediction.device_id == device_id)
        if start is not None:
            query = query.where(SohPrediction.predicted_at >= start)
        if end is not None:
            query = query.where(SohPrediction.predicted_at <= end)
        if cursor is not None:
            pair = tuple_(SohPrediction.predicted_at, SohPrediction.id)
            query = query.where(pair < cursor if descending else pair > cursor)

        if descending:
            query = query.order_by(
                SohPrediction.predicted_at.desc(), SohPrediction.id.desc()
            )
        else:
            query = query.order_by(
                SohPrediction.predicted_at.asc(), SohPrediction.id.asc()
            )
        result = await self._session.execute(query.limit(limit))
        return list(result.scalars().all())

    async def count_predictions(self, device_id: str) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(SohPrediction)
            .where(SohPrediction.device_id == device_id)
        )
        return int(result.scalar_one())

    async def latest_prediction(self, device_id: str) -> SohPrediction | None:
        result = await self._session.execute(
            select(SohPrediction)
            .where(SohPrediction.device_id == device_id)
            .order_by(
                SohPrediction.predicted_at.desc(), SohPrediction.id.desc()
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def recent_predictions(
        self, device_id: str, *, limit: int = 10
    ) -> list[SohPrediction]:
        """The newest ``limit`` predictions, oldest first.

        Ordered like the offline trajectory: the RUL estimator wants the SoH
        series in discharge order with the most recent value last.
        """
        result = await self._session.execute(
            select(SohPrediction)
            .where(SohPrediction.device_id == device_id)
            .order_by(
                SohPrediction.predicted_at.desc(), SohPrediction.id.desc()
            )
            .limit(limit)
        )
        return list(reversed(list(result.scalars().all())))
