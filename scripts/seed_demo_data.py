#!/usr/bin/env python3
"""Seed the local database with realistic demo data.

The test suites leave behind hundreds of throwaway devices
("test device", "anomaly api test", ...), which makes the
dashboard look empty and fake. This script wipes the data tables
and inserts a small, coherent fleet instead: four named packs,
24 h of telemetry, six weeks of cycles with matching SoH
predictions, and an anomaly history with a realistic workflow
trail (acks, corrective actions, resolutions) produced through
the same diagnostics mapping the live detector uses.

Demo data only - never run this against a database you care
about. It truncates every data table first.

Usage (from the repo root, with the backend venv):

    BMS_DATABASE_URL=postgresql+asyncpg://bms:bms@127.0.0.1:5432/bms \
        ~/workspace/.venvs/sge/bin/python scripts/seed_demo_data.py
"""

from __future__ import annotations

import asyncio
import math
import os
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.db.models import (  # noqa: E402
    AnomalyAction,
    AnomalyEvent,
    BatteryCycle,
    Device,
    DeviceAlert,
    ModelRegistry,
    SohPrediction,
    TelemetrySample,
)
from app.services.anomaly_rules import diagnose  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

MODEL_VERSION = "2026.10.1"
FEATURE_SCHEMA = "window-v1"

DEVICES = [
    # device_id, name, cells, nominal Ah, created days ago
    ("PACK-UPS-01", "Data Hall UPS Pack", 16, 100.0, 210),
    ("PACK-SOLAR-02", "Rooftop Solar Storage", 8, 280.0, 160),
    ("PACK-FORK-03", "Warehouse Forklift Pack", 12, 150.0, 320),
    ("PACK-EV-04", "EV Test Bench Pack", 14, 75.0, 95),
]


def wave(t_hours: float, phase: float) -> float:
    """A smooth daily charge/discharge rhythm, 0..1."""
    return 0.5 + 0.5 * math.sin(2 * math.pi * (t_hours / 11.0) + phase)


async def main() -> None:
    url = os.environ.get(
        "BMS_DATABASE_URL", "postgresql+asyncpg://bms:bms@127.0.0.1:5432/bms"
    )
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    rng = random.Random(20261023)
    now = datetime.now(UTC).replace(microsecond=0)

    async with sessions() as session:
        await session.execute(
            text(
                "TRUNCATE reports, anomaly_actions, anomaly_events, "
                "device_alerts, telemetry_rejections, soh_predictions, "
                "battery_cycles, telemetry_samples, devices, model_registry "
                "RESTART IDENTITY CASCADE"
            )
        )

        session.add_all(
            [
                ModelRegistry(
                    model_name="soh_window_all",
                    model_version=MODEL_VERSION,
                    artifact_path="models/soh_window_all.joblib",
                    feature_schema_version=FEATURE_SCHEMA,
                    training_dataset_version="NASA-B0005-B0053",
                    metrics={"mae": 0.019, "rmse": 0.027, "r2": 0.94},
                    is_active=True,
                    created_at=now - timedelta(days=9),
                ),
                ModelRegistry(
                    model_name="soh_window_all",
                    model_version="2026.08.3",
                    artifact_path="models/archive/soh_window_all-2026.08.3.joblib",
                    feature_schema_version=FEATURE_SCHEMA,
                    training_dataset_version="NASA-B0005-B0048",
                    metrics={"mae": 0.024, "rmse": 0.033, "r2": 0.91},
                    is_active=False,
                    created_at=now - timedelta(days=58),
                ),
            ]
        )

        for idx, (device_id, name, cells, capacity, age_days) in enumerate(DEVICES):
            session.add(
                Device(
                    device_id=device_id,
                    name=name,
                    cell_count=cells,
                    sampling_frequency_hz=1.0,
                    chemistry="li_ion",
                    nominal_capacity_ah=capacity,
                    status="active",
                    created_at=now - timedelta(days=age_days),
                    last_seen_at=now,
                    last_aggregated_at=now,
                )
            )
        await session.flush()

        # ---- Telemetry: 24 h at 2-minute spacing per device ----
        phases = {d[0]: i * 1.7 for i, d in enumerate(DEVICES)}
        for device_id, _name, cells, _cap, _age in DEVICES:
            phase = phases[device_id]
            weak_cell = 7 if device_id == "PACK-UPS-01" else None
            for step in range(721):
                t = now - timedelta(hours=24) + timedelta(minutes=2 * step)
                age_h = (now - t).total_seconds() / 3600.0
                soc = wave(age_h, phase)
                base_v = 3.32 + 0.72 * soc
                cells_v = []
                for cell in range(1, cells + 1):
                    v = base_v + rng.uniform(-0.012, 0.012)
                    if weak_cell is not None and cell == weak_cell:
                        # Cell 7 sags progressively over the last 3 hours:
                        # the imbalance the open critical anomaly reports.
                        drift = max(0.0, (3.0 - age_h) / 3.0) if age_h < 3 else 0.0
                        v -= 0.02 + 0.15 * drift
                    cells_v.append(round(v, 3))
                current = round(-38.0 * math.sin(2 * math.pi * (age_h / 11.0) + phase) + rng.uniform(-1.5, 1.5), 2)
                temp = 26.0 + 5.0 * soc + rng.uniform(-0.6, 0.6)
                if device_id == "PACK-SOLAR-02":
                    # Hot afternoon: acknowledged warning territory.
                    temp += max(0.0, 14.0 - abs(age_h - 4.0) * 4.0)
                if device_id == "PACK-FORK-03" and step % 173 == 90:
                    current = round(current + 88.0, 2)  # lift-motor surge
                session.add(
                    TelemetrySample(
                        device_id=device_id,
                        message_id=f"{device_id}-{int(t.timestamp())}",
                        measured_at=t,
                        received_at=t,
                        pack_voltage=round(sum(cells_v), 2),
                        current=current,
                        temperature=round(temp, 1),
                        cell_voltages=cells_v,
                    )
                )
            await session.flush()

        # ---- Cycles + predictions over the past six weeks ----
        cycle_plans = {
            "PACK-UPS-01": (44, 0.985, 0.928),
            "PACK-SOLAR-02": (37, 0.992, 0.951),
            "PACK-FORK-03": (58, 0.978, 0.884),
            "PACK-EV-04": (26, 0.995, 0.963),
        }
        cycle_ids: dict[str, list[int]] = {}
        for device_id, _name, _cells, capacity, _age in DEVICES:
            count, soh_start, soh_end = cycle_plans[device_id]
            reference = capacity * soh_start
            ids: list[int] = []
            for n in range(1, count + 1):
                frac = (n - 1) / max(1, count - 1)
                soh = soh_start + (soh_end - soh_start) * frac + rng.uniform(-0.0015, 0.0015)
                end = now - timedelta(days=(count - n) * 1.05, hours=3)
                cycle = BatteryCycle(
                    device_id=device_id,
                    cycle_number=n,
                    start_time=end - timedelta(hours=4, minutes=20),
                    end_time=end,
                    capacity_ah=round(capacity * soh, 2),
                    reference_capacity_ah=round(reference, 2),
                    # The whole stack stores SoH in percent units
                    # (0-100), matching the model's output; only the
                    # capacity maths above uses the fraction.
                    measured_soh=round(soh * 100, 2),
                    feature_schema_version=FEATURE_SCHEMA,
                )
                session.add(cycle)
                await session.flush()
                ids.append(cycle.id)
                if n >= 5:
                    session.add(
                        SohPrediction(
                            device_id=device_id,
                            cycle_id=cycle.id,
                            predicted_soh=round((soh + rng.uniform(-0.004, 0.004)) * 100, 2),
                            measured_soh=round(soh * 100, 2),
                            model_version=MODEL_VERSION,
                            feature_schema_version=FEATURE_SCHEMA,
                            predicted_at=end + timedelta(minutes=10),
                            input_quality_status="ok",
                        )
                    )
            cycle_ids[device_id] = ids
            await session.flush()

        # ---- Anomaly history with a workflow trail ----
        def event(device_id, atype, severity, cell, detected, *, hours_open=0.0,
                  status="open", observed=None, expected=None, threshold=None,
                  ended=None, outcome=None, evidence=None):
            causes, actions = diagnose(atype)
            ev = AnomalyEvent(
                device_id=device_id,
                anomaly_type=atype,
                severity=severity,
                affected_cell=cell,
                detected_at=detected,
                last_detected_at=ended or (detected + timedelta(hours=hours_open)),
                ended_at=ended,
                observed_value=observed,
                expected_value=expected,
                threshold=threshold,
                duration_seconds=(ended - detected).total_seconds() if ended else hours_open * 3600,
                status=status,
                resolution_outcome=outcome,
                diagnostic_evidence=evidence or {},
                likely_causes=causes,
                recommended_actions=actions,
                rule_version="rules-v3",
            )
            session.add(ev)
            return ev

        def action(ev, atype_, actor, note, when):
            session.add(
                AnomalyAction(
                    anomaly_id=ev.id, action_type=atype_, actor=actor,
                    note=note, created_at=when,
                )
            )

        ev = event("PACK-UPS-01", "cell_voltage_spread", "critical", 7,
                   now - timedelta(hours=2, minutes=40), hours_open=2.65,
                   observed=0.183, expected=0.02, threshold=0.05,
                   evidence={"spread_v": 0.183, "weak_cell": 7, "weak_cell_v": 3.41})
        await session.flush()
        action(ev, "acknowledge", "priya.sharma",
               "Watching cell 7; sag grows under load. Balancing cycle scheduled tonight.",
               now - timedelta(hours=2, minutes=5))

        ev = event("PACK-UPS-01", "temperature_out_of_range", "warning", None,
                   now - timedelta(days=2, hours=3), status="resolved",
                   observed=46.2, expected=38.0, threshold=45.0,
                   ended=now - timedelta(days=2, hours=2, minutes=15),
                   outcome="Vent obstruction cleared; pack temperature normalised within 30 minutes.")
        await session.flush()
        action(ev, "acknowledge", "priya.sharma", "Looks environmental, checking the hall vents.", now - timedelta(days=2, hours=2, minutes=50))
        action(ev, "corrective_action", "facility.ops", "Cleared packaging stacked against the pack vent and reduced charge rate to 0.3C.", now - timedelta(days=2, hours=2, minutes=30))
        action(ev, "resolve", "priya.sharma", "Temperature back under 39C and stable for 30 minutes.", now - timedelta(days=2, hours=2, minutes=15))

        ev = event("PACK-UPS-01", "cell_voltage_jump", "info", 3,
                   now - timedelta(days=5, hours=6), status="investigating",
                   hours_open=1.0, observed=0.09, threshold=0.05,
                   evidence={"delta_v": 0.09, "window_s": 60})
        await session.flush()
        action(ev, "acknowledge", "arjun.mehta", "Single jump, no repeat since. Suspect a loose sense lead; will inspect at next maintenance window.", now - timedelta(days=5, hours=4))

        ev = event("PACK-SOLAR-02", "temperature_out_of_range", "warning", None,
                   now - timedelta(hours=5, minutes=20), status="acknowledged",
                   hours_open=5.3, observed=45.8, expected=39.0, threshold=45.0,
                   evidence={"ambient_c": 38.5, "charge_rate_c": 0.8})
        await session.flush()
        action(ev, "acknowledge", "arjun.mehta", "Heat wave plus full-rate charging. Shading crew scheduled; derating to 0.5C meanwhile.", now - timedelta(hours=4, minutes=45))

        ev = event("PACK-SOLAR-02", "telemetry_gap", "info", None,
                   now - timedelta(days=1, hours=7), status="resolved",
                   observed=22.0, threshold=5.0,
                   ended=now - timedelta(days=1, hours=6, minutes=38),
                   outcome="Gateway rebooted by site power cut; telemetry resumed automatically.")
        await session.flush()
        action(ev, "resolve", "system.monitor", "Device reconnected and backfilled the gap window.", now - timedelta(days=1, hours=6, minutes=38))

        ev = event("PACK-FORK-03", "current_jump", "warning", None,
                   now - timedelta(minutes=42), hours_open=0.7,
                   observed=92.4, expected=18.0, threshold=40.0,
                   evidence={"delta_a": 92.4, "window_s": 5})
        await session.flush()

        ev = event("PACK-FORK-03", "cell_voltage_out_of_range", "critical", 3,
                   now - timedelta(days=1, hours=4), status="investigating",
                   hours_open=6.0, observed=2.86, expected=3.3, threshold=3.0,
                   evidence={"cell_v": 2.86, "pack_min_cell": 3})
        await session.flush()
        action(ev, "acknowledge", "warehouse.lead", "Forklift pulled from rotation pending inspection.", now - timedelta(days=1, hours=3))
        action(ev, "corrective_action", "warehouse.lead", "Swapped cell 3 with a loaner cell; original sent for capacity test.", now - timedelta(hours=20))

        ev = event("PACK-EV-04", "cell_voltage_spread", "warning", 11,
                   now - timedelta(days=3, hours=2), status="resolved",
                   observed=0.072, expected=0.02, threshold=0.05,
                   ended=now - timedelta(days=3, hours=1),
                   outcome="Full balance charge completed; spread back to 0.018 V.")
        await session.flush()
        action(ev, "resolve", "lab.tech", "Balanced and re-measured; spread nominal.", now - timedelta(days=3, hours=1))

        event("PACK-EV-04", "telemetry_gap", "warning", None,
              now - timedelta(hours=1, minutes=5), hours_open=1.05,
              observed=65.0, threshold=5.0,
              evidence={"last_seen_s": 3900})

        event("PACK-SOLAR-02", "cell_voltage_out_of_range", "info", 5,
              now - timedelta(days=6, hours=1), status="resolved",
              observed=4.21, expected=4.1, threshold=4.2,
              ended=now - timedelta(days=6),
              outcome="Charger float voltage trimmed to 4.18 V; no recurrence.")

        event("PACK-FORK-03", "temperature_out_of_range", "warning", None,
              now - timedelta(days=4, hours=5), status="false_positive",
              observed=45.4, threshold=45.0,
              ended=now - timedelta(days=4, hours=4),
              outcome="Sensor found resting against the motor housing; reading was not cell temperature.")

        # ---- Device alerts ----
        session.add_all(
            [
                DeviceAlert(
                    device_id="PACK-UPS-01", rule_code="cell_voltage_spread",
                    severity="critical",
                    message="Cell voltage spread 0.183 V exceeds the 0.05 V limit (cell 7 sagging)",
                    details={"spread_v": 0.183, "cell": 7}, status="active",
                    first_seen_at=now - timedelta(hours=2, minutes=40),
                    last_seen_at=now,
                ),
                DeviceAlert(
                    device_id="PACK-SOLAR-02", rule_code="temperature_high",
                    severity="warning",
                    message="Pack temperature 45.8 C above the 45 C warning limit",
                    details={"temperature_c": 45.8}, status="acked",
                    first_seen_at=now - timedelta(hours=5, minutes=20),
                    last_seen_at=now - timedelta(minutes=30),
                    acked_at=now - timedelta(hours=4, minutes=45),
                    acked_by="arjun.mehta",
                ),
                DeviceAlert(
                    device_id="PACK-FORK-03", rule_code="current_jump",
                    severity="warning",
                    message="Current jumped 92 A in 5 s (lift motor surge)",
                    details={"delta_a": 92.4}, status="active",
                    first_seen_at=now - timedelta(minutes=42),
                    last_seen_at=now - timedelta(minutes=2),
                ),
            ]
        )

        await session.commit()
    await engine.dispose()
    print("demo data seeded")


if __name__ == "__main__":
    asyncio.run(main())
