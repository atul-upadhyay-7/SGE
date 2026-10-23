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
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.logging import get_logger

_settings = get_settings()
logger = get_logger(__name__)


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model."""


def _engine_kwargs() -> dict:
    if _settings.db_use_nullpool:
        return {"poolclass": NullPool}
    return {
        "pool_pre_ping": True,
        "pool_size": _settings.db_pool_size,
        "max_overflow": _settings.db_max_overflow,
        "pool_recycle": _settings.db_pool_recycle_s,
    }


engine: AsyncEngine = create_async_engine(
    _settings.database_url, echo=_settings.db_echo, **_engine_kwargs()
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
    ``(False, "database unreachable")``. Never raises.

    The raw driver error is logged server-side, not returned: the
    health endpoint is reachable without a key in local setups, and
    driver text leaks host, port and authentication detail.
    """
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True, None
    except Exception:  # noqa: BLE001 - health must report any failure
        logger.exception("Database health check failed")
        return False, "database unreachable"
