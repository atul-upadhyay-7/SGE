"""Persistence reads for the model registry (FR-07, CR §7)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelRegistry


class ModelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def active_models(self) -> list[ModelRegistry]:
        """Every registry row flagged active, newest first."""
        result = await self._session.execute(
            select(ModelRegistry)
            .where(ModelRegistry.is_active.is_(True))
            .order_by(ModelRegistry.created_at.desc(), ModelRegistry.id.desc())
        )
        return list(result.scalars().all())
