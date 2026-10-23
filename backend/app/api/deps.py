"""Shared FastAPI dependencies for write access control."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from app.core.config import Settings, get_settings
from app.core.security import (
    OPERATOR_API_KEY_HEADER,
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
