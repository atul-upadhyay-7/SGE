"""Shared FastAPI dependencies for write access control."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, status

from app.core.config import Settings, get_settings
from app.core.security import (
    ADMIN_API_KEY_HEADER,
    OPERATOR_API_KEY_HEADER,
    VIEWER_API_KEY_HEADER,
    hash_credential,
    verify_device_credential,
)


def require_operator_key(
    settings: Settings = Depends(get_settings),
    x_operator_key: str | None = Header(default=None),
) -> None:
    """Guard dashboard write actions when an operator key is configured.

    With ``BMS_OPERATOR_API_KEY`` unset this is a no-op (local development).
    A missing or wrong key is a 401 that does not reveal which part failed.
    """
    if settings.operator_api_key is None:
        return
    if not verify_device_credential(
        hash_credential(settings.operator_api_key), x_operator_key
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"missing or invalid '{OPERATOR_API_KEY_HEADER}' header",
        )


def _matches(configured: str | None, presented: str | None) -> bool:
    return configured is not None and verify_device_credential(
        hash_credential(configured), presented
    )


def require_read_access(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    """Application-wide guard for read requests when a viewer key is set.

    Registered once on the app, so a newly added GET route is protected by
    default. Only the bare liveness probe stays open. Writes are not handled
    here: each write route carries its own guard (device credential, admin
    key or operator key).
    """
    if settings.viewer_api_key is None:
        return
    if request.method not in {"GET", "HEAD"}:
        return
    if request.url.path == f"{settings.api_prefix}/health":
        return
    headers = request.headers
    if (
        _matches(settings.viewer_api_key, headers.get(VIEWER_API_KEY_HEADER))
        or _matches(settings.operator_api_key, headers.get(OPERATOR_API_KEY_HEADER))
        or _matches(settings.admin_api_key, headers.get(ADMIN_API_KEY_HEADER))
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"missing or invalid '{VIEWER_API_KEY_HEADER}' header",
    )
