"""Device registration, lookup and credential rotation (FR-01, NFR: Security)."""

from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import (
    ADMIN_API_KEY_HEADER,
    DEVICE_CREDENTIAL_HEADER,
    hash_credential,
    verify_device_credential,
)
from app.db.database import get_session
from app.repositories.device_repository import DeviceRepository
from app.schemas.device import (
    DeviceCreate,
    DeviceCredentialUpdate,
    DeviceRead,
)
from app.services.event_bus import EVENT_DEVICE

router = APIRouter(prefix="/devices", tags=["devices"])


def require_admin_key(
    settings: Settings = Depends(get_settings),
    x_admin_key: str | None = Header(default=None),
) -> None:
    """Guard management endpoints when an admin key is configured.

    With ``BMS_ADMIN_API_KEY`` unset the check is a no-op, so the local
    stack needs no extra configuration. When it *is* set, a missing or wrong
    key is reported as 401 rather than 403: the endpoint does not advertise
    to an unauthenticated caller that a key is required at all.
    """
    if settings.admin_api_key is None:
        return
    if not verify_device_credential(hash_credential(settings.admin_api_key), x_admin_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"missing or invalid '{ADMIN_API_KEY_HEADER}' header",
        )


@router.post(
    "",
    response_model=DeviceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_key)],
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
    fields = payload.model_dump()
    # Hash at the edge: the plaintext never reaches the ORM, so it cannot be
    # logged by SQLAlchemy echo or written to the quarantine table.
    credential = fields.pop("credential", None)
    if credential:
        fields["credential_ref"] = hash_credential(credential)
    try:
        device = await repository.create(**fields)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"device '{payload.device_id}' already registered",
        ) from None

    request.app.state.device_cache.add(device)
    _publish_device(request, device)
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


@router.put(
    "/{device_id}/credential",
    response_model=DeviceRead,
    dependencies=[Depends(require_admin_key)],
)
async def update_device_credential(
    device_id: str,
    payload: DeviceCredentialUpdate,
    request: Request,
    x_device_credential: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> DeviceRead:
    """Rotate or clear a device's secret.

    While a secret is still set, the caller must present it in the
    ``X-Device-Credential`` header. That makes rotation a self-service
    operation for the device itself without leaving a window where the
    endpoint is unauthenticated: clearing the secret (which *opens* the
    device) still requires the admin key.
    """
    repository = DeviceRepository(session)
    device = await repository.get_by_device_id(device_id)
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"device '{device_id}' not found",
        )

    if device.credential_ref is not None and not verify_device_credential(
        device.credential_ref, x_device_credential
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"missing or invalid '{DEVICE_CREDENTIAL_HEADER}' header",
        )

    credential = payload.credential
    device.credential_ref = (
        hash_credential(credential) if credential else None
    )
    await session.commit()

    request.app.state.device_cache.add(device)
    _publish_device(request, device)
    return DeviceRead.model_validate(device)


def _publish_device(request: Request, device) -> None:
    """Broadcast a device change to live subscribers, if the bus exists."""
    events = getattr(request.app.state, "events", None)
    if events is not None:
        events.publish(
            EVENT_DEVICE,
            {"device_id": device.device_id, "status": device.status},
        )
