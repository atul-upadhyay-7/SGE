"""Provision the read-only Grafana DB login after migrations. No secrets in argv."""
import asyncio
import os

from app.db.database import engine
from sqlalchemy import text


async def main():
    password = os.environ["BMS_GRAFANA_PG_PASSWORD"]
    if not password:
        raise SystemExit("BMS_GRAFANA_PG_PASSWORD must not be empty")
    async with engine.begin() as conn:
        exists = await conn.scalar(text("SELECT 1 FROM pg_roles WHERE rolname='bms_ro'"))
        # PostgreSQL role passwords cannot be bind parameters. Escape using the
        # server's literal quoting, never shell interpolation or string guessing.
        literal = await conn.scalar(text("SELECT quote_literal(:password)"), {"password": password})
        verb = "ALTER" if exists else "CREATE"
        await conn.exec_driver_sql(f"{verb} ROLE bms_ro LOGIN PASSWORD {literal}")
        database = await conn.scalar(text("SELECT quote_ident(current_database())"))
        await conn.execute(text(f"GRANT CONNECT ON DATABASE {database} TO bms_ro"))
        await conn.execute(text("GRANT USAGE ON SCHEMA public TO bms_ro"))
        await conn.execute(text("GRANT SELECT ON ALL TABLES IN SCHEMA public TO bms_ro"))
        await conn.execute(text("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO bms_ro"))
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
