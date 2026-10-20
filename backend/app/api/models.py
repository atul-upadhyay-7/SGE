"""Model registry reads (FR-07, CR §7).

``GET /models/active`` names the model artifact(s) currently served, so a
frontend or report can attribute a prediction to a concrete, reproducible
version without reading the filesystem.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.repositories.model_repository import ModelRepository
from app.schemas.cycle_read import ActiveModels, ModelRead

router = APIRouter(prefix="/models", tags=["models"])


@router.get("/active", response_model=ActiveModels)
async def active_models(
    session: AsyncSession = Depends(get_session),
) -> ActiveModels:
    models = await ModelRepository(session).active_models()
    return ActiveModels(
        count=len(models),
        items=[ModelRead.model_validate(model) for model in models],
    )
