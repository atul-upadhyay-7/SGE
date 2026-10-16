"""Persistence for accepted telemetry and quarantined rejections (FR-02, FR-05).

Idempotency is enforced by the database, not by application bookkeeping:
``INSERT ... ON CONFLICT (device_id, message_id) DO NOTHING`` makes a
QoS-1 redelivery a no-op. A duplicate whose *stored* payload differs from
the incoming one is surfaced as a conflict rather than silently swallowed,
so operators can see that a device reused a message identifier.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TelemetryRejection, TelemetrySample
from app.schemas.telemetry import RejectionRecord, ValidatedSample

_ROUND = 6


@dataclass
class InsertOutcome:
    inserted: int = 0
    duplicates: int = 0
    conflicting: list[ValidatedSample] = field(default_factory=list)


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

        outcome = InsertOutcome(inserted=len(inserted_keys))
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
