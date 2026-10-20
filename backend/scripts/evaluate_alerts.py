"""Evaluate the alert rules for one or all devices, without the monitor loop.

Usage
    python backend/scripts/evaluate_alerts.py --all
    python backend/scripts/evaluate_alerts.py --device-id CYC_8a27a254

Mirrors the loop's behaviour (aggregation first when ``BMS_AUTO_AGGREGATE``
is true, then rule evaluation) so an operator can force a pass without waiting
for the next tick.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.db.database import SessionLocal, engine  # noqa: E402
from app.repositories.device_repository import DeviceRepository  # noqa: E402
from app.services.alert_service import reconcile  # noqa: E402
from app.services.cycle_service import CycleAggregationService  # noqa: E402


async def _process(device_id: str) -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        device = await DeviceRepository(session).get_by_device_id(device_id)
        if device is None:
            print(f"{device_id}: not registered, skipping")
            return
        if settings.auto_aggregate:
            await CycleAggregationService(session).aggregate_device(device_id)
            device.last_aggregated_at = now
        result = await reconcile(session, device, now=now, settings=settings)
        await session.commit()
    firing = ", ".join(result.firing) if result.firing else "none"
    print(
        f"{device_id}: opened={result.opened} updated={result.updated} "
        f"resolved={result.resolved} firing=[{firing}]"
    )


async def _main(args: argparse.Namespace) -> None:
    try:
        if args.device_id:
            await _process(args.device_id)
        else:
            async with SessionLocal() as session:
                devices = await DeviceRepository(session).all_devices()
            if not devices:
                print("no devices registered")
                return
            for device in devices:
                await _process(device.device_id)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--device-id", help="evaluate a single device")
    group.add_argument(
        "--all", action="store_true", help="evaluate every registered device"
    )
    os.environ.setdefault("BMS_MQTT_ENABLED", "false")
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
