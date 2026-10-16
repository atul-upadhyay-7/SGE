"""Device registration and lookup (FR-01)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.repositories.device_repository import DeviceRepository
from app.schemas.device import DeviceCreate, DeviceRead

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post(
    "", response_model=DeviceRead, status_code=status.HTTP_201_CREATED
)
async def register_device(
    payload: DeviceCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> DeviceRead:
    repository = DeviceRepository(session)
    if await repository.get_by_device_id(payload.device_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"device '{payload.device_id}' already registered",
        )
    try:
        device = await repository.create(**payload.model_dump())
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"device '{payload.device_id}' already registered",
        ) from None

    request.app.state.device_cache.add(device)
    return DeviceRead.model_validate(device)


@router.get("", response_model=list[DeviceRead])
async def list_devices(
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[DeviceRead]:
    devices = await DeviceRepository(session).list_all(offset=offset, limit=limit)
    return [DeviceRead.model_validate(device) for device in devices]


@router.get("/{device_id}", response_model=DeviceRead)
async def get_device(
    device_id: str, session: AsyncSession = Depends(get_session)
) -> DeviceRead:
    device = await DeviceRepository(session).get_by_device_id(device_id)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"device '{device_id}' not found",
        )
    return DeviceRead.model_validate(device)
