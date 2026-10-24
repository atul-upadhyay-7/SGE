#!/usr/bin/env python3
"""Seed the demo fleet only when the database has no devices yet.

First-run helper: a fresh install (``docker compose up``, or the host
path in the README) migrates an empty schema and would otherwise show
a dashboard with no devices at all. This wrapper waits for the schema
(the ``devices`` table) to exist, counts devices, and:

- devices present  -> prints that and exits 0, touching nothing;
- no devices       -> runs ``seed_demo_data.py`` as a subprocess.

The demo seed itself truncates every data table before inserting, so
it must never run unconditionally at startup; this wrapper is the
only startup path that calls it, and it does so only on an empty
database. Re-running it is always safe.

Usage (same env as the seed):

    BMS_DATABASE_URL=postgresql+asyncpg://bms:bms@127.0.0.1:5432/bms \
        python scripts/seed_if_empty.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

DATABASE_URL = os.environ.get(
    "BMS_DATABASE_URL", "postgresql+asyncpg://bms:bms@127.0.0.1:5432/bms"
)
WAIT_SECONDS = 90


async def device_count_when_ready() -> int | None:
    """Device count once the schema exists; None if it never appears."""
    engine = create_async_engine(DATABASE_URL)
    try:
        waited = 0
        while waited <= WAIT_SECONDS:
            try:
                async with engine.connect() as conn:
                    table = await conn.scalar(
                        text(
                            "SELECT to_regclass('public.devices')"
                        )
                    )
                    if table is not None:
                        count = await conn.scalar(text("SELECT count(*) FROM devices"))
                        return int(count or 0)
            except Exception:
                # Database itself not accepting connections yet.
                pass
            await asyncio.sleep(2)
            waited += 2
        return None
    finally:
        await engine.dispose()


def main() -> int:
    count = asyncio.run(device_count_when_ready())
    if count is None:
        print(
            f"seed-if-empty: no 'devices' table after {WAIT_SECONDS}s - "
            "run the migrations first (alembic upgrade head, or start bms-api).",
            file=sys.stderr,
        )
        return 1
    if count > 0:
        print(f"seed-if-empty: {count} device(s) already present, skipping seed.")
        return 0
    print("seed-if-empty: empty database, loading the demo fleet...")
    seed = Path(__file__).resolve().with_name("seed_demo_data.py")
    result = subprocess.run([sys.executable, str(seed)], env=os.environ.copy())
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
