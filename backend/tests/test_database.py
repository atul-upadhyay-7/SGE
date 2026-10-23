"""Integration test: the configured database is reachable and migrated.

Skips (rather than fails) when no Postgres is running, so the unit suite
stays runnable without infrastructure.
"""

from __future__ import annotations

import pytest
from app.db.database import SessionLocal, check_connection, engine
from sqlalchemy import text

EXPECTED_TABLES = {
    "devices",
    "telemetry_samples",
    "battery_cycles",
    "soh_predictions",
    "model_registry",
}


@pytest.mark.asyncio
async def test_database_connection_and_schema() -> None:
    ok, detail = await check_connection()
    if not ok:
        pytest.skip(f"Postgres not reachable: {detail}")

    async with SessionLocal() as session:
        rows = await session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        )
        tables = {row[0] for row in rows}

    missing = EXPECTED_TABLES - tables
    assert not missing, f"missing tables after migration: {sorted(missing)}"

    # Dispose so pooled asyncpg connections are not left to be collected on a
    # closed event loop (pytest-asyncio uses a fresh loop per test).
    await engine.dispose()
