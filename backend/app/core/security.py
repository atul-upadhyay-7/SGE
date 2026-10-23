"""Device credential handling (NFR: Security).

A device may be registered with a shared secret. The backend never stores the
secret itself: it stores ``sha256(secret)`` in ``devices.credential_ref`` and
compares incoming secrets against that digest in constant time. A device with
no stored digest is *open* — that is the local-development default and keeps
the anonymous-broker compose stack working unchanged.

Broker-side concerns (TLS listener, per-device ACLs, MQTT username/password)
are deliberately out of this module's scope: they belong to the Mosquitto
configuration, which the README documents. What lives here is the part only
the application can do — deciding whether *this* request or *this* payload
may be attributed to *that* device.
"""

from __future__ import annotations

import hashlib
import hmac

# Header an HTTP client uses to present a device secret. The MQTT path
# carries the same secret in the payload's optional ``credential`` field.
DEVICE_CREDENTIAL_HEADER = "X-Device-Credential"
ADMIN_API_KEY_HEADER = "X-Admin-Key"
OPERATOR_API_KEY_HEADER = "X-Operator-Key"
VIEWER_API_KEY_HEADER = "X-Viewer-Key"

# Minimum length for a device secret. Device secrets are typically minted
# with a CSPRNG rather than chosen by a human, so the floor is a hygiene
# guard, not a password-policy statement.
MIN_CREDENTIAL_LENGTH = 8
MAX_CREDENTIAL_LENGTH = 128


def hash_credential(raw: str) -> str:
    """Return the hex digest stored in ``devices.credential_ref``.

    SHA-256 without a salt is appropriate here because the input is a
    high-entropy random secret, not a human-chosen password: there is no
    meaningful dictionary to precompute against. It is *not* appropriate for
    user passwords — do not reuse this helper for those.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_device_credential(stored_ref: str | None, presented: str | None) -> bool:
    """Check a presented secret against a device's stored digest.

    * ``stored_ref is None`` → the device is open; anything is accepted.
    * ``presented is None``  → nothing was sent; only open devices pass.
    * otherwise              → constant-time digest comparison.

    The digest is compared with :func:`hmac.compare_digest` rather than ``==``
    so that a timing probe cannot recover the stored digest byte by byte.
    """
    if stored_ref is None:
        return True
    if not presented:
        return False
    return hmac.compare_digest(hash_credential(presented), stored_ref)


def is_protected(stored_ref: str | None) -> bool:
    """True when a device requires a credential (i.e. one is configured)."""
    return stored_ref is not None
