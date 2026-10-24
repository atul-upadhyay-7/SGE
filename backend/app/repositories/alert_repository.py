"""Persistence for device alerts (Phase 5).

Opening, updating and resolving alerts is done here so the evaluator stays a
pure decision function: the repository owns the "one open alert per
(device, rule)" invariant, backed by the partial unique index
``uq_alert_open_per_device_rule``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DeviceAlert

_MAX_PAGE = 1000


class AlertRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def open_alert(
        self,
        *,
        device_id: str,
        rule_code: str,
        severity: str,
        message: str,
        details: dict | None,
        seen_at: datetime,
    ) -> DeviceAlert:
        """Insert a new alert, or refresh the existing open one for this rule.

        The ``ON CONFLICT`` targets the partial unique index, so a rule that
        is already open bumps ``last_seen_at`` instead of stacking a second
        row. The returned object is always a live ORM row.
        """
        statement = (
            pg_insert(DeviceAlert)
            .values(
                device_id=device_id,
                rule_code=rule_code,
                severity=severity,
                message=message[:512],
                details=details,
                status="active",
                first_seen_at=seen_at,
                last_seen_at=seen_at,
            )
            .on_conflict_do_update(
                index_elements=["device_id", "rule_code"],
                index_where=text("status <> 'resolved'"),
                set_={
                    "severity": severity,
                    "message": message[:512],
                    "details": details,
                    "last_seen_at": seen_at,
                    # A rule that had been acknowledged and is still firing
                    # stays acknowledged; only a new opening resets it.
                    "status": func.coalesce(DeviceAlert.status, "active"),
                },
            )
            .returning(DeviceAlert)
        )
        result = await self._session.execute(statement)
        return result.scalar_one()

    async def resolve_alerts_not_in(
        self, device_id: str, keep_rule_codes: set[str], seen_at: datetime
    ) -> int:
        """Resolve every open alert for this device whose rule is not firing."""
        result = await self._session.execute(
            update(DeviceAlert)
            .where(
                DeviceAlert.device_id == device_id,
                DeviceAlert.status != "resolved",
                ~DeviceAlert.rule_code.in_(keep_rule_codes),
            )
            .values(status="resolved", resolved_at=seen_at)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)

    async def open_alerts(self, device_id: str) -> list[DeviceAlert]:
        result = await self._session.execute(
            select(DeviceAlert)
            .where(
                DeviceAlert.device_id == device_id,
                DeviceAlert.status != "resolved",
            )
            .order_by(DeviceAlert.rule_code)
        )
        return list(result.scalars().all())

    async def get_by_id(self, alert_id: int) -> DeviceAlert | None:
        result = await self._session.execute(
            select(DeviceAlert).where(DeviceAlert.id == alert_id)
        )
        return result.scalar_one_or_none()

    async def ack_alert(
        self, alert_id: int, *, acked_by: str, acked_at: datetime
    ) -> DeviceAlert | None:
        # Conditional UPDATE rather than read-modify-write: the
        # evaluator's resolve sweep must not be undone by an ack
        # that read the alert just before it resolved.
        result = await self._session.execute(
            update(DeviceAlert)
            .where(
                DeviceAlert.id == alert_id,
                DeviceAlert.status != "resolved",
            )
            .values(status="acked", acked_by=acked_by, acked_at=acked_at)
        )
        await self._session.flush()
        if (result.rowcount or 0) == 0:
            return None
        return await self.get_by_id(alert_id)

    async def list_alerts(
        self,
        *,
        device_id: str | None = None,
        status_filter: str | None = None,
        severity: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[DeviceAlert]:
        query = select(DeviceAlert)
        if device_id is not None:
            query = query.where(DeviceAlert.device_id == device_id)
        if status_filter is not None:
            query = query.where(DeviceAlert.status == status_filter)
        if severity is not None:
            query = query.where(DeviceAlert.severity == severity)
        result = await self._session.execute(
            query.order_by(DeviceAlert.last_seen_at.desc(), DeviceAlert.id.desc())
            .offset(offset)
            .limit(min(limit, _MAX_PAGE))
        )
        return list(result.scalars().all())

    async def count_alerts(
        self,
        *,
        device_id: str | None = None,
        status_filter: str | None = None,
        severity: str | None = None,
    ) -> int:
        query = select(func.count()).select_from(DeviceAlert)
        if device_id is not None:
            query = query.where(DeviceAlert.device_id == device_id)
        if status_filter is not None:
            query = query.where(DeviceAlert.status == status_filter)
        if severity is not None:
            query = query.where(DeviceAlert.severity == severity)
        result = await self._session.execute(query)
        return int(result.scalar_one())
