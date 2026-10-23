"""Dedicated test-database resolution with a fail-closed guard.

The backend suite contains tests that DROP and recreate every table
(``Base.metadata.drop_all``). They must never run against an application
database (a demo dataset or a deployment). Contract: the suite only runs
against a database whose name contains ``test``.

Resolution order:
1. ``BMS_TEST_DATABASE_URL`` - explicit override; must already name a test
   database, and must not name the same database as ``BMS_DATABASE_URL``
   (same name on the same server), otherwise the suite refuses to start
   (fail closed). A ``_test`` suffix alone is not proof of isolation.
2. ``BMS_DATABASE_URL`` - the database name is rewritten to ``<name>_test``,
   so ``pytest`` next to a running demo stack can never touch the demo data.
"""

from __future__ import annotations

import os

DEFAULT_URL = "postgresql+asyncpg://bms:bms@localhost:5433/bms"
TEST_MARKER = "test"


def split_url(url: str) -> tuple[str, str, str]:
    """Return (base-without-db, database name, query) for a Postgres URL."""
    base, _, query = url.partition("?")
    prefix, _, name = base.rpartition("/")
    return prefix, name, query


def resolve_test_database_url(env: dict[str, str] | None = None) -> str:
    """The database URL the test suite is allowed to use."""
    env = os.environ if env is None else env
    explicit = env.get("BMS_TEST_DATABASE_URL")
    base = env.get("BMS_DATABASE_URL", DEFAULT_URL)
    if explicit:
        explicit_prefix, name, _ = split_url(explicit)
        if TEST_MARKER not in name.lower():
            raise RuntimeError(
                "BMS_TEST_DATABASE_URL must name a dedicated test database "
                f"(name containing 'test'), got {name!r}. Refusing to run: "
                "tests drop and recreate tables."
            )
        base_prefix, base_name, _ = split_url(base)
        if (explicit_prefix, name) == (base_prefix, base_name):
            raise RuntimeError(
                "BMS_TEST_DATABASE_URL names the same database "
                f"({name!r} on the same server) as BMS_DATABASE_URL. A name "
                "ending in '_test' is not proof of isolation. Refusing to "
                "run: tests drop and recreate tables."
            )
        return explicit
    prefix, name, query = split_url(base)
    if TEST_MARKER not in name.lower():
        name = f"{name}_test"
    url = f"{prefix}/{name}"
    return f"{url}?{query}" if query else url


def database_name(url: str) -> str:
    return split_url(url)[1]


async def ensure_test_database(url: str) -> None:
    """Best-effort creation of the resolved test database.

    Uses the same server/credentials against the ``postgres`` maintenance
    database. If the role may not create databases the suite still runs when
    the database already exists, and otherwise fails with a clear message.
    """
    import asyncpg  # local import: only needed at session setup

    prefix, name, query = split_url(url)
    admin_url = f"{prefix}/postgres" + (f"?{query}" if query else "")
    # asyncpg wants a plain postgresql:// URL, not the SQLAlchemy dialect.
    admin_dsn = admin_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    try:
        conn = await asyncpg.connect(admin_dsn)
        try:
            await conn.execute(f'CREATE DATABASE "{name}"')
        except asyncpg.DuplicateDatabaseError:
            pass
        finally:
            await conn.close()
    except asyncpg.InvalidCatalogNameError:
        raise RuntimeError(
            f"Test database {name!r} does not exist and could not be created. "
            "Create it once, e.g. CREATE DATABASE %s;" % name
        )
