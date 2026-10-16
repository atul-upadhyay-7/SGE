"""Persistence operations for devices (FR-01)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Device


class DeviceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_device_id(self, device_id: str) -> Device | None:
        result = await self._session.execute(
            select(Device).where(Device.device_id == device_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self, offset: int = 0, limit: int = 100) -> list[Device]:
        result = await self._session.execute(
            select(Device)
            .order_by(Device.device_id)
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def create(self, **fields: object) -> Device:
        device = Device(**fields)
        self._session.add(device)
        await self._session.flush()
        return device

    async def all_devices(self) -> list[Device]:
        result = await self._session.execute(select(Device))
        return list(result.scalars().all())

    async def touch_last_seen(self, device_id: str, seen_at: datetime) -> None:
        """Advance ``last_seen_at`` monotonically; out-of-order data never rewinds it."""
        await self._session.execute(
            update(Device)
            .where(Device.device_id == device_id)
            .values(
                last_seen_at=func.greatest(
                    func.coalesce(Device.last_seen_at, seen_at), seen_at
                )
            )
        )
