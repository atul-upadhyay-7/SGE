"""Device authentication helpers.

Phase 1 ships the credential-lookup seam only. Per-device credential
verification, MQTT ACLs and TLS-terminated broker auth (NFR: Security) are
wired in a later phase; keeping the interface here stops that work from
being scattered across the API and consumer.
"""

from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)


def hash_credential(raw: str) -> str:
    """Return a stable hash for storage in ``devices.credential_ref``.

    Not used for verification yet; present so credential handling has one
    home from the start.
    """
    import hashlib

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_device_credential(stored_ref: str | None, presented: str | None) -> bool:
    """Placeholder device-credential check.

    Returns ``True`` while no credentials are configured, so local
    development against an anonymous broker keeps working. Implemented in
    the hardening phase.
    """
    if stored_ref is None:
        return True
    return False
