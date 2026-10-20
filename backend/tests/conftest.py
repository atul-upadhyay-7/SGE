"""Test configuration for the backend.

Environment is set *before* ``app.main`` is imported because settings are
read (and cached) at import time. MQTT is disabled so the ASGI lifespan does
not try to reach a broker; the database URL points at the compose Postgres
published on host port 5433.
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("BMS_MQTT_ENABLED", "false")
os.environ.setdefault("BMS_MONITOR_ENABLED", "false")
os.environ.setdefault(
    "BMS_DATABASE_URL", "postgresql+asyncpg://bms:bms@localhost:5433/bms"
)
os.environ.setdefault("BMS_DB_USE_NULLPOOL", "true")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client() -> TestClient:
    """A TestClient with the app lifespan (startup/shutdown) running."""
    with TestClient(app) as test_client:
        yield test_client


def db_ready() -> bool:
    """True when the configured Postgres is reachable."""
    from app.db.database import check_connection

    try:
        ok, _ = asyncio.run(check_connection())
    except Exception:  # noqa: BLE001
        return False
    return ok


@pytest.fixture(scope="session")
def require_db() -> bool:
    """Skip a test when no database is available (keeps the unit suite portable)."""
    if not db_ready():
        pytest.skip("Postgres not reachable")
    return True
