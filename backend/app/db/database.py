"""Async SQLAlchemy engine and session management.

Postgres is the sole datastore (PRD §3). The engine is created from the
same ``database_url`` the Alembic environment uses, so migrations and the
running app always target one schema.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

_settings = get_settings()


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model."""


engine: AsyncEngine = create_async_engine(
    _settings.database_url,
    echo=_settings.db_echo,
    pool_pre_ping=True,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    pool_recycle=_settings.db_pool_recycle_s,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped session."""
    async with SessionLocal() as session:
        yield session


async def check_connection() -> tuple[bool, str | None]:
    """Probe the database for the health endpoint.

    Returns ``(True, None)`` when a trivial query succeeds, otherwise
    ``(False, "<reason>")``. Never raises.
    """
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # noqa: BLE001 - health must report any failure
        return False, str(exc)
