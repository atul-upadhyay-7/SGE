"""Measure API responsiveness while large reports build.

Seeds LOAD_* devices with telemetry straight into Postgres, then probes a cheap
endpoint (GET /health/database) while N reports are generated either inside the
request (sync) or by the background worker (queued). Prints p50/p95/max probe
latency and report completion times as JSON, so the numbers can be recorded.

Usage (API running, BMS_DATABASE_URL set for the seed step):
    python scripts/load_test_reports.py --api http://127.0.0.1:8000/api/v1 \
        --devices 5 --samples 5000 --reports 3 [--viewer-key K --operator-key K]
Everything it writes is labelled LOAD_*; --cleanup removes it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def seed(devices: int, samples: int) -> None:
    engine = create_async_engine(os.environ["BMS_DATABASE_URL"])
    async with engine.begin() as conn:
        for d in range(devices):
            device_id = f"LOAD_{d:03d}"
            await conn.execute(
                text(
                    "INSERT INTO devices (device_id, name, cell_count, sampling_frequency_hz, "
                    "chemistry, status, created_at) "
                    "VALUES (:d, 'load test device', 3, 1.0, 'li_ion', 'active', now()) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"d": device_id},
            )
            await conn.execute(
                text(
                    "INSERT INTO telemetry_samples (device_id, message_id, measured_at, "
                    "received_at, pack_voltage, current, temperature, cell_voltages) "
                    "SELECT CAST(:d AS varchar), CAST(:d AS text) || '-' || g, now() - (g || ' seconds')::interval, now(), "
                    "11.1 + (g % 50) / 100.0, -1.2, 28 + (g % 30) / 10.0, "
                    "jsonb_build_array(3.7, 3.7, 3.7) FROM generate_series(1, :n) g "
                    "ON CONFLICT DO NOTHING"
                ),
                {"d": device_id, "n": samples},
            )
    await engine.dispose()


async def cleanup() -> None:
    engine = create_async_engine(os.environ["BMS_DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM telemetry_samples WHERE device_id LIKE 'LOAD_%'"))
        await conn.execute(text("DELETE FROM reports WHERE device_id LIKE 'LOAD_%' OR generated_by = 'load-test'"))
        await conn.execute(text("DELETE FROM devices WHERE device_id LIKE 'LOAD_%'"))
    await engine.dispose()


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * p))] * 1000, 1)


async def probe_loop(client: httpx.AsyncClient, stop: asyncio.Event, out: list[float]) -> None:
    while not stop.is_set():
        started = time.perf_counter()
        try:
            await client.get("/health/database", timeout=60)
            out.append(time.perf_counter() - started)
        except httpx.HTTPError:
            out.append(60.0)
        await asyncio.sleep(0.05)


async def run(mode: str, args, headers: dict) -> dict:
    async with httpx.AsyncClient(base_url=args.api, headers=headers) as client:
        latencies: list[float] = []
        stop = asyncio.Event()
        prober = asyncio.create_task(probe_loop(client, stop, latencies))
        await asyncio.sleep(1.0)
        baseline = list(latencies)
        latencies.clear()

        started = time.perf_counter()
        body = {"generated_by": "load-test", "queued": mode == "queued"}

        async def one(i: int) -> float:
            t0 = time.perf_counter()
            r = await client.post("/reports", json=body, timeout=300)
            r.raise_for_status()
            if mode == "queued":
                rid = r.json()["id"]
                while True:
                    s = (await client.get(f"/reports/{rid}", timeout=60)).json()["status"]
                    if s in {"ready", "failed"}:
                        assert s == "ready", s
                        break
                    await asyncio.sleep(0.2)
            return time.perf_counter() - t0

        done = await asyncio.gather(*(one(i) for i in range(args.reports)))
        total = time.perf_counter() - started
        stop.set()
        await prober
    return {
        "mode": mode,
        "reports": args.reports,
        "baseline_probe_p50_ms": pct(baseline, 0.5) if baseline else None,
        "under_load_probe_p50_ms": pct(latencies, 0.5) if latencies else None,
        "under_load_probe_p95_ms": pct(latencies, 0.95) if latencies else None,
        "under_load_probe_max_ms": round(max(latencies) * 1000, 1) if latencies else None,
        "probes": len(latencies),
        "report_complete_s": [round(x, 2) for x in done],
        "mean_report_s": round(statistics.mean(done), 2),
        "wall_s": round(total, 2),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--devices", type=int, default=5)
    parser.add_argument("--samples", type=int, default=5000)
    parser.add_argument("--reports", type=int, default=3)
    parser.add_argument("--viewer-key")
    parser.add_argument("--operator-key")
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    if args.cleanup:
        await cleanup()
        print("cleaned LOAD_* data")
        return
    headers = {}
    if args.viewer_key:
        headers["X-Viewer-Key"] = args.viewer_key
    if args.operator_key:
        headers["X-Operator-Key"] = args.operator_key
    await seed(args.devices, args.samples)
    results = [await run(mode, args, headers) for mode in ("sync", "queued")]
    print(json.dumps({"devices": args.devices, "samples_per_device": args.samples, "results": results}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
