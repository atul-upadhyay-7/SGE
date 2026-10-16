"""Simulated ESP32 telemetry publisher (FR-02 acceptance).

Publishes canonical PRD telemetry to ``bms/<device_id>/telemetry`` so the
ingestion path can be exercised end to end without hardware. The device must
already be registered (``POST /api/v1/devices``) with a matching
``cell_count``.

Usage::

    python scripts/simulate_publisher.py --device-id BMS_001 --cell-count 3
    python scripts/simulate_publisher.py --hz 5 --count 20 --chaos 0.1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from datetime import UTC, datetime

import aiomqtt


def _configure_event_loop() -> None:
    """paho/aiomqtt need the selector loop on Windows (Proactor lacks add_writer)."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def build_payload(device_id: str, cell_count: int, seq: int, chaos: float) -> dict:
    cells = [round(random.uniform(3.75, 4.10), 3) for _ in range(cell_count)]
    temperature = round(random.uniform(20.0, 38.0), 2)
    current = round(random.uniform(-5.0, 5.0), 3)

    if chaos and random.random() < chaos:
        # Inject a single "impossible" cell to prove rejection + quarantine.
        cells[random.randrange(cell_count)] = 9.99

    return {
        "schema_version": 1,
        "message_id": f"{device_id}-{seq:08d}",
        "device_id": device_id,
        "measured_at": datetime.now(UTC).isoformat(),
        "pack_voltage": round(sum(cells), 3),
        "current": current,
        "temperature": temperature,
        "cell_voltages": cells,
    }


async def run(args: argparse.Namespace) -> None:
    topic = f"{args.prefix}/{args.device_id}/telemetry"
    interval = 1.0 / args.hz
    sent = 0

    async with aiomqtt.Client(
        hostname=args.host,
        port=args.port,
        identifier=f"{args.device_id}-simulator",
    ) as client:
        print(f"Publishing to {topic} at {args.hz} Hz (ctrl-c to stop)")
        while args.count == 0 or sent < args.count:
            payload = build_payload(
                args.device_id, args.cell_count, sent, args.chaos
            )
            await client.publish(topic, json.dumps(payload), qos=args.qos)
            sent += 1
            if sent % max(1, int(args.hz)) == 0:
                print(f"  sent {sent} message(s)")
            await asyncio.sleep(interval)
    print(f"Done: {sent} message(s)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulated BMS telemetry publisher")
    parser.add_argument("--device-id", default="BMS_001")
    parser.add_argument("--cell-count", type=int, default=3)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--prefix", default="bms")
    parser.add_argument("--hz", type=float, default=1.0)
    parser.add_argument("--qos", type=int, default=1)
    parser.add_argument(
        "--count", type=int, default=0, help="0 = run until interrupted"
    )
    parser.add_argument(
        "--chaos",
        type=float,
        default=0.0,
        help="probability of injecting an impossible value (0..1)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    _configure_event_loop()
    try:
        asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        pass
