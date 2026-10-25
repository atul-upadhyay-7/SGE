"""Cycle aggregation and SoH inference for a device (FR-06, FR-07).

Replays a device's persisted telemetry, one sample at a time, through the
*offline streaming tracker* (:class:`streaming.cycle_tracker.CycleTracker`).
That tracker is the single source of truth for cycle boundaries and per-cycle
features: the same class, fed the same samples, produces the same feature row
whether it arrived from a ``.mat`` file or one MQTT message per second. The
backend keeps no private copy of the boundary or feature logic.

For every completed discharge the tracker returns a row that already carries
the partial-window inputs the model consumes (``vw_*`` and ``current_mean``);
it is passed straight to :func:`app.ml.estimator.predict_soh`. The cell used
for a multi-cell device is the *weakest* cell (lowest voltage) of each
sample, because the pack's health is set by the cell that degrades first.

Aggregation is a full, idempotent replay: cycles are upserted on
``(device_id, cycle_number)`` and a device's predictions are replaced, so
running it twice never double-counts.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.db.models import TelemetrySample
from app.ml import registry
from app.ml.estimator import (
    OUT_OF_TRAINED_RANGE,
    WINDOW_FEATURES,
    WINDOW_MIN_REFERENCE_DISCHARGES,
    WINDOW_REFERENCE_WINDOW,
    InsufficientReferenceError,
    WindowRangeError,
    predict_soh,
)
from app.repositories.cycle_repository import CycleRepository
from app.repositories.telemetry_repository import TelemetryRepository
from streaming.cycle_tracker import CycleTracker  # noqa: E402  (sys.path set above)

# Samples read per page while replaying history. Bounds memory on long streams
# without changing the result: the tracker is fed every sample in order.
_REPLAY_PAGE = 5000

_OWNED_PHASES = ("discharge", "idle")


@dataclass
class AggregationResult:
    """Counts from one device aggregation."""

    device_id: str
    samples: int = 0
    cycles: int = 0
    predictions: int = 0
    refused: int = 0


class CycleAggregationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._cycle_repo = CycleRepository(session)
        self._telemetry_repo = TelemetryRepository(session)

    async def aggregate_device(self, device_id: str) -> AggregationResult:
        """Replay, persist cycles, then score SoH per cycle.

        Writes only — the caller owns the transaction (mirrors the device
        endpoints, which commit explicitly).

        A per-device advisory lock serialises concurrent aggregations
        (manual endpoint vs monitor tick): predictions are replaced by
        delete+insert with no natural key, so two interleaved runs would
        double the prediction history. The lock is transaction-scoped —
        it is held until the caller commits or rolls back.
        """
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key)::bigint)"),
            {"lock_key": f"bms-aggregate:{device_id}"},
        )
        cycle_rows, samples = await self._replay(device_id)

        reference = _reference_capacity(cycle_rows)
        values = _cycle_values(cycle_rows, reference)
        await self._cycle_repo.upsert_cycles(device_id, values)
        await self._session.flush()

        id_map = await self._cycle_repo.cycle_id_map(device_id)
        predictions, refused = await _predictions(
            device_id, cycle_rows, id_map, _measured_soh(values)
        )

        await self._cycle_repo.delete_predictions(device_id)
        await self._cycle_repo.insert_predictions(predictions)
        await registry.ensure_registered(self._session)
        await self._session.flush()

        return AggregationResult(
            device_id=device_id,
            samples=samples,
            cycles=len(cycle_rows),
            predictions=len(predictions),
            refused=refused,
        )

    async def _replay(self, device_id: str) -> tuple[list[tuple[dict, datetime]], int]:
        """Feed every stored sample to a fresh tracker.

        Returns ``(cycle_rows, sample_count)`` where each entry pairs the
        tracker's feature row with the timestamp the discharge ended at.
        """
        tracker = CycleTracker(cell_id=device_id)
        rows: list[tuple[dict, datetime]] = []
        samples = 0
        last_owned: datetime | None = None

        async for sample in self._stream_samples(device_id):
            samples += 1
            cell = _representative_cell(sample)
            if cell is None:
                continue

            emitted = tracker.add_sample(
                timestamp=sample.measured_at.timestamp(),
                voltage=cell,
                current=float(sample.current),
                temperature=float(sample.temperature),
            )

            # The cycle owns the sample only while it is discharging or
            # resting; the charge sample that closes it is not part of it, so
            # the last owned timestamp is the discharge's true end.
            if tracker.phase in _OWNED_PHASES:
                last_owned = sample.measured_at

            if emitted is not None:
                duration = _finite(emitted.get("discharge_duration_s")) or 0.0
                if duration >= 10.0:
                    rows.append((emitted, last_owned or sample.measured_at))

        tail = tracker.flush()
        if tail is not None:
            duration = _finite(tail.get("discharge_duration_s")) or 0.0
            if duration >= 10.0:
                rows.append((tail, last_owned or sample.measured_at))

        return rows, samples

    async def _stream_samples(self, device_id: str) -> AsyncIterator[TelemetrySample]:
        """Page ordered samples so a long history is never held at once."""
        cursor: tuple[datetime, int] | None = None
        while True:
            batch = await self._telemetry_repo.list_samples(
                device_id, descending=False, limit=_REPLAY_PAGE, cursor=cursor
            )
            if not batch:
                return
            for sample in batch:
                yield sample
            if len(batch) < _REPLAY_PAGE:
                return
            last = batch[-1]
            cursor = (last.measured_at, last.id)


def _representative_cell(sample: TelemetrySample) -> float | None:
    """The weakest (lowest-voltage) cell of a sample, or ``None`` if absent."""
    cells = sample.cell_voltages
    if not cells:
        return None
    value = min(float(cell) for cell in cells)
    return value if math.isfinite(value) else None


def _finite(value: object) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _reference_capacity(
    cycle_rows: list[tuple[dict, datetime]],
) -> float | None:
    """The device's opening-cycle capacity, the baseline SoH is measured from."""
    for row, _ in cycle_rows:
        if int(row.get("cycle", 0)) == 1:
            return _finite(row.get("capacity_ah"))
    first = cycle_rows[0][0] if cycle_rows else None
    return _finite(first.get("capacity_ah")) if first else None


def _cycle_values(
    cycle_rows: list[tuple[dict, datetime]], reference: float | None
) -> list[dict]:
    """Turn tracker rows into ``battery_cycles`` column dicts.

    ``end_time`` is the last sample the cycle owned; ``start_time`` is derived
    from the tracker's exact ``discharge_duration_s``. The two may sit a few
    seconds from the true boundary because the close is debounced, but the
    span always equals the measured discharge duration.
    """
    values: list[dict] = []
    for row, end_time in cycle_rows:
        capacity = _finite(row.get("capacity_ah"))
        duration = _finite(row.get("discharge_duration_s")) or 0.0
        measured_soh = (
            capacity / reference * 100.0
            if capacity is not None and reference
            else None
        )
        values.append(
            {
                "cycle_number": int(row["cycle"]),
                "start_time": end_time - timedelta(seconds=duration),
                "end_time": end_time,
                "capacity_ah": capacity,
                "reference_capacity_ah": reference,
                "measured_soh": measured_soh,
                "feature_schema_version": registry.FEATURE_SCHEMA_VERSION,
            }
        )
    return values


def _measured_soh(values: list[dict]) -> dict[int, float | None]:
    return {v["cycle_number"]: v["measured_soh"] for v in values}


async def _predictions(
    device_id: str,
    cycle_rows: list[tuple[dict, datetime]],
    id_map: dict[int, int],
    measured: dict[int, float | None],
) -> tuple[list[dict], int]:
    """Score each cycle against the cell's opening discharges.

    The opening discharges are the cell's first ``WINDOW_REFERENCE_WINDOW``
    cycles, exactly as the offline training reference is defined, so the
    backend does not reinterpret the model's baseline. A cell with fewer than
    ``WINDOW_MIN_REFERENCE_DISCHARGES`` cycles cannot be baselined: every cycle
    is refused rather than scored from a guess.
    """
    opening = [row for row, _ in cycle_rows[:WINDOW_REFERENCE_WINDOW]]

    if len(opening) < WINDOW_MIN_REFERENCE_DISCHARGES:
        return [], len(cycle_rows)

    model_version = registry.model_version()
    rows: list[dict] = []
    refused = 0

    for row, end_time in cycle_rows:
        try:
            # XGBoost inference is synchronous CPU work; running it
            # inline would stall the single event loop that also
            # serves ingestion, the API and MQTT (see app/main.py).
            predicted = await run_in_threadpool(predict_soh, row, opening)
        except (InsufficientReferenceError, WindowRangeError, ValueError):
            refused += 1
            continue

        # A discharge whose inputs leave the trained range comes back
        # as this sentinel, not a number: refuse it rather than store
        # a value the model has no basis for.
        if predicted == OUT_OF_TRAINED_RANGE:
            refused += 1
            continue

        number = int(row["cycle"])
        rows.append(
            {
                "device_id": device_id,
                "cycle_id": id_map.get(number),
                "predicted_soh": predicted,
                "measured_soh": measured.get(number),
                "model_version": model_version,
                "feature_schema_version": registry.FEATURE_SCHEMA_VERSION,
                "predicted_at": end_time,
                "input_quality_status": (
                    "ok" if _window_complete(row) else "imputed"
                ),
            }
        )

    return rows, refused


def _window_complete(row: dict) -> bool:
    return all(_finite(row.get(name)) is not None for name in WINDOW_FEATURES)
