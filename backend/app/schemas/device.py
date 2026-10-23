"""Device registration and credential schemas (FR-01, NFR: Security)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.security import (
    MAX_CREDENTIAL_LENGTH,
    MIN_CREDENTIAL_LENGTH,
)


class DeviceCredentialMixin(BaseModel):
    """Write-only device secret.

    The plaintext is accepted here and only here; it is hashed before storage
    and never appears in any response schema, so a leaked API response can
    never leak a secret.
    """

    credential: str | None = Field(
        default=None,
        min_length=MIN_CREDENTIAL_LENGTH,
        max_length=MAX_CREDENTIAL_LENGTH,
        description=(
            "Optional shared secret for the device. Stored only as a SHA-256 "
            "digest; never returned by the API. Leave null for an open device."
        ),
    )

    @field_validator("credential")
    @classmethod
    def _reject_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        return stripped


class DeviceCreate(DeviceCredentialMixin):
    """Request body for registering a device.

    ``cell_count`` is required and authoritative: it is the parameter the
    ingestion validator checks each telemetry payload against (FR-04).
    """

    # Topic-safe ids only: the MQTT consumer addresses a device as one
    # segment of ``bms/<id>/telemetry``, so an id containing '/', '+',
    # '#' or spaces could be registered but never publish successfully.
    device_id: str = Field(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
    )
    name: str = Field(min_length=1, max_length=255)
    cell_count: int = Field(ge=1, le=64)
    sampling_frequency_hz: float = Field(default=1.0, gt=0, le=100.0)
    # Must name a known profile in services/anomaly_rules.py: an unknown
    # value used to be accepted here and then silently scored with the
    # li_ion thresholds, which is the wrong safety envelope for e.g. an
    # LFP pack registered with a typo.
    chemistry: str = Field(
        default="li_ion",
        max_length=32,
        pattern=r"^(li_ion|lifepo4|nimh|lead_acid)$",
    )
    nominal_capacity_ah: float | None = Field(default=None, gt=0)
    status: str = Field(default="active", pattern="^(active|inactive|retired)$")


class DeviceCredentialUpdate(DeviceCredentialMixin):
    """Body for rotating (or clearing) a device's secret.

    ``credential: null`` removes the secret and re-opens the device. The
    caller must present the *current* secret in the
    ``X-Device-Credential`` header when one is still set.
    """


class DeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: str
    name: str
    cell_count: int
    sampling_frequency_hz: float
    chemistry: str
    nominal_capacity_ah: float | None = None
    status: str
    created_at: datetime | None = None
    last_seen_at: datetime | None = None
    # ``credential_ref`` is deliberately absent: digests are as sensitive as
    # the secrets they replace for an attacker doing offline cracking.
