"""Simulated ESP32 telemetry publisher (FR-02 acceptance).

Publishes canonical PRD telemetry to ``bms/<device_id>/telemetry`` so the
ingestion path can be exercised end to end without hardware. The device must
already be registered (``POST /api/v1/devices``) with a matching
``cell_count``.

Usage::

    python scripts/simulate_publisher.py --device-id BMS_001 --cell-count 3
    python scripts/simulate_publisher.py --fleet --hz 1
    python scripts/simulate_publisher.py --hz 5 --count 20 --chaos 0.1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import sys
from datetime import UTC, datetime

import aiomqtt

DEFAULT_FLEET = [
    ("PACK-UPS-01", 16),
    ("PACK-SOLAR-02", 8),
    ("PACK-FORK-03", 12),
    ("PACK-EV-04", 14),
]


def _configure_event_loop() -> None:
    """paho/aiomqtt need the selector loop on Windows (Proactor lacks add_writer)."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def build_payload(device_id: str, cell_count: int, seq: int, chaos: float) -> dict:
    now = datetime.now(UTC)
    base_v = random.uniform(3.75, 4.05)
    cells = [round(base_v + random.uniform(-0.015, 0.015), 3) for _ in range(cell_count)]

    # In the demo fleet scenario, PACK-UPS-01 has a weak cell 7 that sags slightly
    if device_id == "PACK-UPS-01" and cell_count >= 7:
        cells[6] = round(max(cells[6] - 0.06, 3.20), 3)

    temperature = round(random.uniform(22.0, 34.0), 2)
    # Smooth, continuous discharge load with realistic operating variation
    t_sec = seq
    current = round(-12.5 + 2.0 * math.sin(t_sec / 60.0) + random.uniform(-0.4, 0.4), 2)

    if chaos and random.random() < chaos:
        # Inject a single "impossible" cell to prove rejection + quarantine.
        cells[random.randrange(cell_count)] = 9.99

    return {
        "schema_version": 1,
        "message_id": f"{device_id}-{int(now.timestamp())}-{seq:06d}",
        "device_id": device_id,
        "measured_at": now.isoformat(),
        "pack_voltage": round(sum(cells), 3),
        "current": current,
        "temperature": temperature,
        "cell_voltages": cells,
    }


async def run(args: argparse.Namespace) -> None:
    targets = (
        DEFAULT_FLEET
        if args.fleet
        else [(args.device_id, args.cell_count)]
    )

    interval = 1.0 / args.hz
    sent = 0

    client_id = "fleet-simulator" if args.fleet else f"{args.device_id}-simulator"

    while True:
        try:
            async with aiomqtt.Client(
                hostname=args.host,
                port=args.port,
                identifier=client_id,
            ) as client:
                desc = "demo fleet (4 packs)" if args.fleet else f"{args.device_id}"
                print(f"Publishing live telemetry for {desc} at {args.hz} Hz to {args.host}:{args.port}")
                while args.count == 0 or sent < args.count:
                    for device_id, cell_count in targets:
                        topic = f"{args.prefix}/{device_id}/telemetry"
                        payload = build_payload(device_id, cell_count, sent, args.chaos)
                        await client.publish(topic, json.dumps(payload), qos=args.qos)
                    sent += 1
                    if sent % max(1, int(args.hz * 5)) == 0:
                        print(f"  sent {sent} message cycle(s)")
                    await asyncio.sleep(interval)
                break
        except (aiomqtt.MqttError, OSError) as exc:
            print(f"MQTT connection error: {exc}. Retrying in 2s...")
            await asyncio.sleep(2.0)
    print(f"Done: {sent} message cycle(s)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulated BMS telemetry publisher")
    parser.add_argument("--device-id", default="BMS_001")
    parser.add_argument("--cell-count", type=int, default=3)
    parser.add_argument("--fleet", action="store_true", help="publish for all 4 demo fleet devices")
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
