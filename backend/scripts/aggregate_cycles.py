"""Aggregate discharge cycles and refresh SoH for one or all devices (FR-06/07).

Runs the same :class:`CycleAggregationService` the API uses, against the
configured database. Idempotent: re-running replaces a device's cycles and
predictions in place, so it is safe to call after new telemetry lands.

Usage::

    python backend/scripts/aggregate_cycles.py --device-id BMS_001
    python backend/scripts/aggregate_cycles.py --all
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import SessionLocal, engine  # noqa: E402
from app.repositories.device_repository import DeviceRepository  # noqa: E402
from app.services.cycle_service import (  # noqa: E402
    AggregationResult,
    CycleAggregationService,
)


async def _aggregate(device_id: str) -> AggregationResult:
    async with SessionLocal() as session:
        result = await CycleAggregationService(session).aggregate_device(device_id)
        await session.commit()
        return result


async def _resolve_device_ids(args: argparse.Namespace) -> list[str]:
    if args.device_id:
        return [args.device_id]
    async with SessionLocal() as session:
        devices = await DeviceRepository(session).all_devices()
    return [device.device_id for device in devices]


async def run(args: argparse.Namespace) -> int:
    device_ids = await _resolve_device_ids(args)
    if not device_ids:
        print("No devices registered; nothing to aggregate.")
        return 0

    for device_id in device_ids:
        result = await _aggregate(device_id)
        print(
            f"{result.device_id}: {result.samples} sample(s), "
            f"{result.cycles} cycle(s), {result.predictions} prediction(s), "
            f"{result.refused} refused"
        )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate cycles and refresh SoH predictions"
    )
    parser.add_argument(
        "--device-id", default=None, help="aggregate one device"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="aggregate every registered device (default when no id given)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    async def _main() -> int:
        try:
            return await run(parse_args())
        finally:
            await engine.dispose()

    raise SystemExit(asyncio.run(_main()))
