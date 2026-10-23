"""Persistence for accepted telemetry and quarantined rejections (FR-02, FR-05).

Idempotency is enforced by the database, not by application bookkeeping:
``INSERT ... ON CONFLICT (device_id, message_id) DO NOTHING`` makes a
QoS-1 redelivery a no-op. A duplicate whose *stored* payload differs from
the incoming one is surfaced as a conflict rather than silently swallowed,
so operators can see that a device reused a message identifier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Device, TelemetryRejection, TelemetrySample
from app.schemas.telemetry import RejectionRecord, ValidatedSample

_ROUND = 6


@dataclass
class InsertOutcome:
    inserted: int = 0
    duplicates: int = 0
    conflicting: list[ValidatedSample] = field(default_factory=list)
    # (device_id, message_id) pairs this call actually inserted, so the
    # caller can publish/act on exactly those rows — never on duplicates.
    inserted_keys: set[tuple[str, str]] = field(default_factory=set)
    # Set by the ingestion flush when the transaction failed and was
    # rolled back: nothing in this outcome was persisted.
    failed: bool = False


class TelemetryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_batch(self, samples: list[ValidatedSample]) -> InsertOutcome:
        if not samples:
            return InsertOutcome()

        values = [
            {
                "device_id": s.device_id,
                "message_id": s.message_id,
                "measured_at": s.measured_at,
                "pack_voltage": s.pack_voltage,
                "current": s.current,
                "temperature": s.temperature,
                "cell_voltages": s.cell_voltages,
                "additional_data": s.additional_data,
                "quality_flags": s.quality_flags,
            }
            for s in samples
        ]
        statement = (
            pg_insert(TelemetrySample)
            .values(values)
            .on_conflict_do_nothing(index_elements=["device_id", "message_id"])
            .returning(TelemetrySample.device_id, TelemetrySample.message_id)
        )
        result = await self._session.execute(statement)
        inserted_keys = {(row.device_id, row.message_id) for row in result.all()}

        outcome = InsertOutcome(
            inserted=len(inserted_keys), inserted_keys=set(inserted_keys)
        )
        duplicates = [
            s for s in samples if (s.device_id, s.message_id) not in inserted_keys
        ]
        outcome.duplicates = len(duplicates)
        if duplicates:
            outcome.conflicting = await self._find_conflicting(duplicates)
        return outcome

    async def _find_conflicting(
        self, duplicates: list[ValidatedSample]
    ) -> list[ValidatedSample]:
        device_ids = {s.device_id for s in duplicates}
        message_ids = {s.message_id for s in duplicates}
        result = await self._session.execute(
            select(
                TelemetrySample.device_id,
                TelemetrySample.message_id,
                TelemetrySample.measured_at,
                TelemetrySample.pack_voltage,
                TelemetrySample.current,
                TelemetrySample.temperature,
                TelemetrySample.cell_voltages,
            ).where(
                TelemetrySample.device_id.in_(device_ids),
                TelemetrySample.message_id.in_(message_ids),
            )
        )
        stored = {(row.device_id, row.message_id): row for row in result.all()}

        conflicting: list[ValidatedSample] = []
        for sample in duplicates:
            row = stored.get((sample.device_id, sample.message_id))
            if row is not None and not _matches(row, sample):
                conflicting.append(sample)
        return conflicting

    async def latest_sample(self, device_id: str) -> TelemetrySample | None:
        """The most recent sample by measured time (id breaks ties)."""
        result = await self._session.execute(
            select(TelemetrySample)
            .where(TelemetrySample.device_id == device_id)
            .order_by(TelemetrySample.measured_at.desc(), TelemetrySample.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_samples(
        self,
        device_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        descending: bool = True,
        limit: int = 100,
        cursor: tuple[datetime, int] | None = None,
    ) -> list[TelemetrySample]:
        """A page of samples, optionally bounded by time and resume cursor.

        Ordering is by ``(measured_at, id)``; the cursor is an exclusive bound
        on that pair, so a page never repeats or skips rows even while new
        samples land between requests.
        """
        query = select(TelemetrySample).where(TelemetrySample.device_id == device_id)
        if start is not None:
            query = query.where(TelemetrySample.measured_at >= start)
        if end is not None:
            query = query.where(TelemetrySample.measured_at <= end)
        if cursor is not None:
            pair = tuple_(TelemetrySample.measured_at, TelemetrySample.id)
            query = query.where(pair < cursor if descending else pair > cursor)

        if descending:
            query = query.order_by(
                TelemetrySample.measured_at.desc(), TelemetrySample.id.desc()
            )
        else:
            query = query.order_by(
                TelemetrySample.measured_at.asc(), TelemetrySample.id.asc()
            )
        result = await self._session.execute(query.limit(limit))
        return list(result.scalars().all())

    async def count_samples(self, device_id: str) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(TelemetrySample)
            .where(TelemetrySample.device_id == device_id)
        )
        return int(result.scalar_one())

    async def measured_at_bounds(
        self, device_id: str
    ) -> tuple[datetime | None, datetime | None]:
        result = await self._session.execute(
            select(
                func.min(TelemetrySample.measured_at),
                func.max(TelemetrySample.measured_at),
            ).where(TelemetrySample.device_id == device_id)
        )
        first, last = result.one()
        return first, last

    async def count_rejections_by_reason(self, device_id: str | None, *, start: datetime | None = None, end: datetime | None = None) -> dict[str, int]:
        filters = []
        if device_id is not None:
            filters.append(TelemetryRejection.device_id == device_id)
        if start is not None:
            filters.append(TelemetryRejection.received_at >= start)
        if end is not None:
            filters.append(TelemetryRejection.received_at <= end)
        result = await self._session.execute(
            select(TelemetryRejection.reason, func.count())
            .where(*filters)
            .group_by(TelemetryRejection.reason)
            .order_by(TelemetryRejection.reason)
        )
        return {reason: int(count) for reason, count in result.all()}

    async def window_stats(
        self,
        device_id: str | None,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict:
        """Aggregate sample statistics over a time window (report §4.7 items 3-4).

        Reduced in SQL — count, coverage bounds and the voltage/current/
        temperature min/max/mean — so a report over a large history never loads
        the readings themselves.
        """
        filters = []
        if device_id is not None:
            filters.append(TelemetrySample.device_id == device_id)
        if start is not None:
            filters.append(TelemetrySample.measured_at >= start)
        if end is not None:
            filters.append(TelemetrySample.measured_at <= end)

        def agg(column):
            return (
                func.min(column).label("min"),
                func.max(column).label("max"),
                func.avg(column).label("mean"),
            )

        result = (
            await self._session.execute(
                select(
                    func.count().label("count"),
                    func.min(TelemetrySample.measured_at).label("first"),
                    func.max(TelemetrySample.measured_at).label("last"),
                    *[
                        stat
                        for column in (
                            TelemetrySample.pack_voltage,
                            TelemetrySample.current,
                            TelemetrySample.temperature,
                        )
                        for stat in agg(column)
                    ],
                ).where(*filters)
            )
        ).one()

        def block(offset: int) -> dict:
            lo = result[3 + offset * 3]
            hi = result[4 + offset * 3]
            mean = result[5 + offset * 3]
            return {
                "min": float(lo) if lo is not None else None,
                "max": float(hi) if hi is not None else None,
                "mean": float(mean) if mean is not None else None,
            }

        return {
            "sample_count": int(result[0]),
            "first_measured_at": result[1],
            "last_measured_at": result[2],
            "pack_voltage": block(0),
            "current": block(1),
            "temperature": block(2),
        }

    async def gap_summary(self, device_id: str | None, *, start: datetime | None = None, end: datetime | None = None) -> dict:
        """Observed internal gaps, not unobserved leading/trailing coverage.

        Partition by device so fleet aggregation never pairs different packs.
        SQL window avoids loading raw telemetry into report memory.
        """
        filters = []
        if device_id is not None:
            filters.append(TelemetrySample.device_id == device_id)
        if start is not None:
            filters.append(TelemetrySample.measured_at >= start)
        if end is not None:
            filters.append(TelemetrySample.measured_at <= end)
        paired = select(TelemetrySample.device_id.label('device_id'), TelemetrySample.measured_at.label('at'), func.lag(TelemetrySample.measured_at).over(partition_by=TelemetrySample.device_id, order_by=TelemetrySample.measured_at).label('previous')).where(*filters).subquery()
        delta = func.extract('epoch', paired.c.at - paired.c.previous)
        threshold = func.greatest(30.0, 3.0 / func.nullif(Device.sampling_frequency_hz, 0))
        rows = await self._session.execute(select(func.count(), func.max(delta), func.sum(delta)).select_from(paired).join(Device, Device.device_id == paired.c.device_id).where(paired.c.previous.is_not(None), delta > threshold))
        count, longest, total = rows.one()
        return {'count': int(count), 'longest_gap_seconds': float(longest or 0), 'total_gap_seconds': float(total or 0), 'definition': 'Internal consecutive-sample intervals > max(30s, 3 expected sample intervals), per device. Leading/trailing absence is not inferred.'}

    async def insert_rejections(self, records: list[RejectionRecord]) -> int:
        if not records:
            return 0
        self._session.add_all(
            [
                TelemetryRejection(
                    device_id=r.device_id,
                    message_id=r.message_id,
                    topic=r.topic,
                    source=r.source,
                    reason=r.reason,
                    detail=(r.detail or "")[:1024] or None,
                    raw_payload=r.raw_payload,
                )
                for r in records
            ]
        )
        await self._session.flush()
        return len(records)


def _matches(row: object, sample: ValidatedSample) -> bool:
    return (
        _ts(row.measured_at) == _ts(sample.measured_at)
        and _r(row.pack_voltage) == _r(sample.pack_voltage)
        and _r(row.current) == _r(sample.current)
        and _r(row.temperature) == _r(sample.temperature)
        and [_r(v) for v in (row.cell_voltages or [])]
        == [_r(v) for v in sample.cell_voltages]
    )


def _r(value: float | None) -> float | None:
    return None if value is None else round(float(value), _ROUND)


def _ts(value: object) -> float | None:
    return None if value is None else value.timestamp()
