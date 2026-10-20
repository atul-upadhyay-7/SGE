"""Device-level RUL lookup (FR-08).

Thin orchestration: read the device's newest predicted SoH values in
discharge order and hand them to the reference-trajectory estimator. No
state is stored — RUL is derived, exactly like an alert, so a model change
or a re-aggregation is reflected on the next request rather than leaving a
stale number behind.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.ml.rul_estimator import (
    DEFAULT_THRESHOLD_PCT,
    RulEstimate,
    RulUnavailableError,
    estimate_rul,
)
from app.repositories.cycle_repository import CycleRepository

# A device with no SoH predictions yet has nothing to match. Reported as its
# own status rather than a 404: the device exists, the estimate is merely
# not ready.
STATUS_NO_PREDICTIONS = "no_predictions"


@dataclass(frozen=True, slots=True)
class SohPoint:
    """One input to the trajectory match, as the API reports it."""

    predicted_soh: float
    predicted_at: datetime | None
    cycle_id: int | None


@dataclass(frozen=True, slots=True)
class RulResult:
    """An estimate plus the SoH series that produced it."""

    estimate: RulEstimate
    points: tuple[SohPoint, ...]
    total_predictions: int


class RulService:
    def __init__(
        self, session: AsyncSession, settings: Settings | None = None
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()

    async def estimate_for_device(
        self, device_id: str
    ) -> RulResult:
        """Estimate RUL for one device from its stored predictions."""
        limit = max(1, self._settings.rul_history_window)
        repository = CycleRepository(self._session)
        predictions = await repository.recent_predictions(device_id, limit=limit)
        total = await repository.count_predictions(device_id)

        points = tuple(
            SohPoint(
                predicted_soh=float(prediction.predicted_soh),
                predicted_at=prediction.predicted_at,
                cycle_id=prediction.cycle_id,
            )
            for prediction in predictions
        )

        if not points:
            return RulResult(
                estimate=RulEstimate(
                    status=STATUS_NO_PREDICTIONS,
                    rul_discharges=None,
                    p10_discharges=None,
                    p90_discharges=None,
                    threshold_soh_pct=DEFAULT_THRESHOLD_PCT,
                    reference_count=0,
                    history_count=0,
                    history=(),
                ),
                points=(),
                total_predictions=0,
            )

        estimate = estimate_rul(
            [point.predicted_soh for point in points],
            path=self._settings.rul_reference_path,
        )
        return RulResult(
            estimate=estimate, points=points, total_predictions=total
        )


__all__ = [
    "RulResult",
    "RulService",
    "RulUnavailableError",
    "SohPoint",
    "STATUS_NO_PREDICTIONS",
]
