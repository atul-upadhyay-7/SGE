"""Estimate remaining useful life (RUL) for one or all devices.

Usage
    python backend/scripts/estimate_rul.py --all
    python backend/scripts/estimate_rul.py --device-id CYC_8a27a254

Reads the stored SoH predictions and matches them against the offline
reference trajectories, exactly like ``GET /devices/{id}/rul``. Read-only:
it never aggregates or writes.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.database import SessionLocal, engine  # noqa: E402
from app.repositories.device_repository import DeviceRepository  # noqa: E402
from app.services.rul_service import RulService  # noqa: E402


async def _report(device_id: str) -> None:
    async with SessionLocal() as session:
        device = await DeviceRepository(session).get_by_device_id(device_id)
        if device is None:
            print(f"{device_id}: not registered, skipping")
            return
        result = await RulService(session).estimate_for_device(device_id)

    estimate = result.estimate
    if estimate.rul_discharges is None:
        print(f"{device_id}: status={estimate.status}")
        return
    band = (
        f"[{estimate.p10_discharges:.0f}, {estimate.p90_discharges:.0f}]"
        if estimate.p10_discharges is not None
        and estimate.p90_discharges is not None
        else "[n/a]"
    )
    print(
        f"{device_id}: status={estimate.status} "
        f"rul={estimate.rul_discharges} discharges band={band} "
        f"at SoH<{estimate.threshold_soh_pct:.0f}% "
        f"(refs={estimate.reference_count}, history={estimate.history_count})"
    )


async def _main(args: argparse.Namespace) -> None:
    try:
        if args.device_id:
            await _report(args.device_id)
        else:
            async with SessionLocal() as session:
                devices = await DeviceRepository(session).all_devices()
            if not devices:
                print("no devices registered")
                return
            for device in devices:
                await _report(device.device_id)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--device-id", help="estimate for a single device")
    group.add_argument(
        "--all", action="store_true", help="estimate for every registered device"
    )
    os.environ.setdefault("BMS_MQTT_ENABLED", "false")
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
