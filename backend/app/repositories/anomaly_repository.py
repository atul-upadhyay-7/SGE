"""Persistence and aggregation for anomaly events and their actions (CR §3–§6).

The recurrence rule (CR §6) lives here: :meth:`open_or_extend` looks up the
single open event for ``(device_id, anomaly_type, affected_cell)`` and either
advances it (a repeated detection extends one episode) or inserts a new one.
The partial unique index ``uq_anomaly_open_per_key`` is the guarantee behind
it, so a 1 Hz anomalous reading can never mint one event per sample.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AnomalyAction, AnomalyEvent


@dataclass
class EventDelta:
    """The outcome of one detection pass for a rule key."""

    opened: list[AnomalyEvent]
    extended: list[AnomalyEvent]
    resolved: list[AnomalyEvent]


class AnomalyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- detection write path ---------------------------------------------

    async def open_event(
        self,
        device_id: str,
        anomaly_type: str,
        affected_cell: int | None,
    ) -> AnomalyEvent | None:
        """The currently open event for a rule key, if any."""
        cell_match = (
            AnomalyEvent.affected_cell.is_(None)
            if affected_cell is None
            else AnomalyEvent.affected_cell == affected_cell
        )
        result = await self._session.execute(
            select(AnomalyEvent).where(
                AnomalyEvent.device_id == device_id,
                AnomalyEvent.anomaly_type == anomaly_type,
                cell_match,
                AnomalyEvent.status == "open",
            )
        )
        return result.scalars().first()

    async def open_events(self, device_id: str) -> list[AnomalyEvent]:
        """Every open event for a device, keyed for a reconcile pass."""
        result = await self._session.execute(
            select(AnomalyEvent)
            .where(
                AnomalyEvent.device_id == device_id,
                AnomalyEvent.status == "open",
            )
            .order_by(AnomalyEvent.id.asc())
        )
        return list(result.scalars().all())

    async def open_or_extend(self, fields: dict) -> AnomalyEvent:
        """Open a new episode, or extend the existing one for the same key.

        ``fields`` must contain the identity columns plus the detection detail
        (severity, observed/expected/threshold, evidence, causes, actions,
        detected_at). On extend, the *identity* is kept and the detail is
        refreshed so the episode reflects its worst/latest observation.
        """
        existing = await self.open_event(
            fields["device_id"], fields["anomaly_type"], fields.get("affected_cell")
        )
        if existing is not None:
            existing.last_detected_at = fields["last_detected_at"]
            existing.duration_seconds = fields["duration_seconds"]
            existing.severity = fields["severity"]
            existing.observed_value = fields["observed_value"]
            existing.expected_value = fields["expected_value"]
            existing.threshold = fields["threshold"]
            existing.diagnostic_evidence = fields["diagnostic_evidence"]
            existing.likely_causes = fields["likely_causes"]
            existing.recommended_actions = fields["recommended_actions"]
            existing.rule_version = fields["rule_version"]
            await self._session.flush()
            return existing

        event = AnomalyEvent(**fields)
        self._session.add(event)
        await self._session.flush()
        return event

    async def close_event(
        self, event: AnomalyEvent, ended_at: datetime
    ) -> AnomalyEvent:
        event.status = "resolved"
        event.ended_at = ended_at
        span = (ended_at - event.detected_at).total_seconds()
        event.duration_seconds = max(span, event.duration_seconds)
        self._session.add(
            AnomalyAction(
                anomaly_id=event.id,
                action_type="resolve",
                actor="detector",
                note="condition cleared",
            )
        )
        await self._session.flush()
        return event

    # --- reads ------------------------------------------------------------

    async def get(self, anomaly_id: int) -> AnomalyEvent | None:
        result = await self._session.execute(
            select(AnomalyEvent).where(AnomalyEvent.id == anomaly_id)
        )
        return result.scalar_one_or_none()

    async def list_events(
        self,
        *,
        device_id: str | None = None,
        severity: str | None = None,
        anomaly_type: str | None = None,
        status: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[AnomalyEvent], int]:
        """A filtered page of events plus the unpaged total for the same filter."""
        filters = []
        if device_id is not None:
            filters.append(AnomalyEvent.device_id == device_id)
        if severity is not None:
            filters.append(AnomalyEvent.severity == severity)
        if anomaly_type is not None:
            filters.append(AnomalyEvent.anomaly_type == anomaly_type)
        if status is not None:
            filters.append(AnomalyEvent.status == status)
        if start is not None:
            filters.append(AnomalyEvent.detected_at >= start)
        if end is not None:
            filters.append(AnomalyEvent.detected_at <= end)

        total = int(
            (
                await self._session.execute(
                    select(func.count()).select_from(AnomalyEvent).where(*filters)
                )
            ).scalar_one()
        )
        rows = await self._session.execute(
            select(AnomalyEvent)
            .where(*filters)
            .order_by(AnomalyEvent.detected_at.desc(), AnomalyEvent.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(rows.scalars().all()), total

    async def summary_rows(
        self,
        *,
        device_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict:
        """Counts by status and severity/type, plus bounds and mean duration."""
        filters = _range_filters(device_id, start, end)

        async def count_by(column) -> list[tuple[str, int]]:
            result = await self._session.execute(
                select(column, func.count())
                .where(*filters)
                .group_by(column)
                .order_by(func.count().desc())
            )
            return [(str(k), int(v)) for k, v in result.all()]

        status_counts = dict(await count_by(AnomalyEvent.status))
        severity_counts = await count_by(AnomalyEvent.severity)
        type_counts = await count_by(AnomalyEvent.anomaly_type)

        bounds = (
            await self._session.execute(
                select(
                    func.min(AnomalyEvent.detected_at),
                    func.max(AnomalyEvent.last_detected_at),
                    func.avg(AnomalyEvent.duration_seconds),
                ).where(*filters)
            )
        ).one()

        return {
            "status_counts": status_counts,
            "severity_counts": severity_counts,
            "type_counts": type_counts,
            "first_detected_at": bounds[0],
            "last_detected_at": bounds[1],
            "avg_duration_seconds": (
                float(bounds[2]) if bounds[2] is not None else None
            ),
            "total": sum(status_counts.values()),
        }

    async def hourly(
        self,
        *,
        device_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict]:
        """Event counts grouped by hour of ``detected_at`` (CR §6)."""
        filters = _range_filters(device_id, start, end)
        bucket = func.date_trunc("hour", AnomalyEvent.detected_at).label("hour")
        result = await self._session.execute(
            select(
                bucket,
                func.count().label("total"),
                func.count().filter(AnomalyEvent.status == "open").label("open"),
                func.count().filter(AnomalyEvent.status == "resolved").label(
                    "resolved"
                ),
            )
            .where(*filters)
            .group_by(bucket)
            .order_by(bucket)
        )
        return [
            {
                "hour": row.hour,
                "total": int(row.total),
                "open": int(row.open),
                "resolved": int(row.resolved),
            }
            for row in result.all()
        ]

    async def severity_by_hour(
        self,
        *,
        device_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[datetime, dict[str, int]]:
        """Per-hour severity breakdown, keyed for merging into :meth:`hourly`."""
        filters = _range_filters(device_id, start, end)
        bucket = func.date_trunc("hour", AnomalyEvent.detected_at).label("hour")
        result = await self._session.execute(
            select(bucket, AnomalyEvent.severity, func.count())
            .where(*filters)
            .group_by(bucket, AnomalyEvent.severity)
        )
        breakdown: dict[datetime, dict[str, int]] = {}
        for hour, severity, count in result.all():
            breakdown.setdefault(hour, {})[severity] = int(count)
        return breakdown

    async def recurrence(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Repeated events by ``(device, type, cell)`` (CR §6)."""
        filters = []
        if start is not None:
            filters.append(AnomalyEvent.detected_at >= start)
        if end is not None:
            filters.append(AnomalyEvent.detected_at <= end)
        result = await self._session.execute(
            select(
                AnomalyEvent.device_id,
                AnomalyEvent.anomaly_type,
                AnomalyEvent.affected_cell,
                func.count().label("occurrences"),
                func.min(AnomalyEvent.detected_at).label("first_detected_at"),
                func.max(AnomalyEvent.last_detected_at).label("last_detected_at"),
                func.max(AnomalyEvent.duration_seconds).label("max_duration_seconds"),
                func.count()
                .filter(AnomalyEvent.status != "resolved")
                .label("unresolved"),
            )
            .where(*filters)
            .group_by(
                AnomalyEvent.device_id,
                AnomalyEvent.anomaly_type,
                AnomalyEvent.affected_cell,
            )
            .order_by(func.count().desc())
            .limit(limit)
        )
        return [
            {
                "device_id": row.device_id,
                "anomaly_type": row.anomaly_type,
                "affected_cell": row.affected_cell,
                "occurrences": int(row.occurrences),
                "first_detected_at": row.first_detected_at,
                "last_detected_at": row.last_detected_at,
                "max_duration_seconds": float(row.max_duration_seconds or 0.0),
                "unresolved": int(row.unresolved),
            }
            for row in result.all()
        ]

    # --- actions ----------------------------------------------------------

    async def add_action(
        self,
        anomaly_id: int,
        action_type: str,
        *,
        actor: str | None = None,
        note: str | None = None,
        evidence: dict | None = None,
    ) -> AnomalyAction:
        action = AnomalyAction(
            anomaly_id=anomaly_id,
            action_type=action_type,
            actor=actor,
            note=note,
            evidence=evidence,
        )
        self._session.add(action)
        await self._session.flush()
        return action

    async def list_actions(self, anomaly_id: int) -> list[AnomalyAction]:
        result = await self._session.execute(
            select(AnomalyAction)
            .where(AnomalyAction.anomaly_id == anomaly_id)
            .order_by(AnomalyAction.created_at.asc(), AnomalyAction.id.asc())
        )
        return list(result.scalars().all())

    async def set_status(self, anomaly_id: int, status: str) -> None:
        await self._session.execute(
            update(AnomalyEvent)
            .where(AnomalyEvent.id == anomaly_id)
            .values(status=status)
        )


def _range_filters(
    device_id: str | None, start: datetime | None, end: datetime | None
) -> list:
    filters = []
    if device_id is not None:
        filters.append(AnomalyEvent.device_id == device_id)
    if start is not None:
        filters.append(AnomalyEvent.detected_at >= start)
    if end is not None:
        filters.append(AnomalyEvent.detected_at <= end)
    return filters
