"""Remaining Useful Life / End-of-Life endpoint (FR-08).

Computed on demand from the device's stored SoH predictions and the offline
reference curves, so there is no extra table to keep in sync: re-aggregating
a device (or swapping the reference curves) is reflected on the next request.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.ml.rul_estimator import RulUnavailableError
from app.repositories.device_repository import DeviceRepository
from app.schemas.rul import RulRead, SohPointRead
from app.services.rul_service import RulService

router = APIRouter(prefix="/devices/{device_id}", tags=["rul"])


@router.get("/rul", response_model=RulRead)
async def device_rul(
    device_id: str,
    session: AsyncSession = Depends(get_session),
) -> RulRead:
    """Estimate how many discharges remain before retirement.

    Matches the device's recent predicted SoH against the offline reference
    trajectories (``models/rul_reference_curves.json``) and reports the
    median remaining discharges plus the p10/p90 spread across references.
    A device with no predictions yet returns ``status="no_predictions"``
    with null numbers rather than a 404: the device is known, the estimate
    is simply not ready.
    """
    device = await DeviceRepository(session).get_by_device_id(device_id)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"device '{device_id}' not registered",
        )

    try:
        result = await RulService(session).estimate_for_device(device_id)
    except RulUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    estimate = result.estimate
    return RulRead(
        device_id=device_id,
        status=estimate.status,
        rul_discharges=estimate.rul_discharges,
        p10_discharges=estimate.p10_discharges,
        p90_discharges=estimate.p90_discharges,
        threshold_soh_pct=estimate.threshold_soh_pct,
        reference_count=estimate.reference_count,
        history_count=estimate.history_count,
        total_predictions=result.total_predictions,
        history=[
            SohPointRead(
                predicted_soh=point.predicted_soh,
                predicted_at=point.predicted_at,
                cycle_id=point.cycle_id,
            )
            for point in result.points
        ],
    )
