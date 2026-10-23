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

# The suite drops and recreates tables; it must never touch an application
# database. Resolve (and, when missing, create) a dedicated test database
# before app.main is imported, because settings are cached at import time.
from db_guard import database_name, ensure_test_database, resolve_test_database_url  # noqa: E402

os.environ["BMS_DATABASE_URL"] = resolve_test_database_url()
asyncio.run(ensure_test_database(os.environ["BMS_DATABASE_URL"]))
# A freshly created test database has no schema; migrations are idempotent,
# so applying them here keeps the suite self-contained either way.
import subprocess  # noqa: E402
import sys  # noqa: E402

subprocess.run(
    [sys.executable, "-m", "alembic", "upgrade", "head"],
    check=True,
    env=os.environ.copy(),
    capture_output=True,
)
os.environ.setdefault("BMS_DB_USE_NULLPOOL", "true")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _require_test_database() -> None:
    """Fail closed if the app was somehow configured away from a test DB."""
    from app.core.config import get_settings

    name = database_name(get_settings().database_url)
    if "test" not in name.lower():
        pytest.exit(
            f"Backend tests may only run against a dedicated test database; "
            f"configured database is {name!r}. Aborting before any table is dropped."
        )


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
        if os.environ.get("BMS_REQUIRE_DB") == "true":
            pytest.fail("Postgres is required for this run but not reachable")
        pytest.skip("Postgres not reachable")
    return True
