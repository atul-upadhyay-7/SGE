"""Tests for device credentials, HTTP ingestion auth and the admin key (Phase 6).

Two layers are covered:

* pure unit tests for :mod:`app.core.security` and the shared validation path
  (no database, exactly how the MQTT hot path runs), and
* end-to-end API tests against Postgres for registration, rotation and the
  authenticated ingestion call.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from app.core.config import get_settings
from app.core.security import (
    ADMIN_API_KEY_HEADER,
    DEVICE_CREDENTIAL_HEADER,
    hash_credential,
    is_protected,
    verify_device_credential,
)
from app.schemas.telemetry import RejectionRecord, ValidatedSample
from app.services import ingestion_service as ing
from app.services.device_cache import DeviceCache
from app.services.ingestion_service import IngestionService

# --------------------------------------------------------------------------
# app.core.security
# --------------------------------------------------------------------------


def test_open_device_accepts_anything() -> None:
    assert verify_device_credential(None, None) is True
    assert verify_device_credential(None, "whatever") is True


def test_protected_device_rejects_missing_and_wrong_secrets() -> None:
    stored = hash_credential("correct-horse")
    assert verify_device_credential(stored, None) is False
    assert verify_device_credential(stored, "") is False
    assert verify_device_credential(stored, "wrong-battery") is False


def test_protected_device_accepts_the_right_secret() -> None:
    stored = hash_credential("correct-horse")
    assert verify_device_credential(stored, "correct-horse") is True


def test_digest_is_stable_and_not_the_plaintext() -> None:
    digest = hash_credential("correct-horse")
    assert digest == hash_credential("correct-horse")
    assert digest != "correct-horse"
    assert len(digest) == 64  # sha256 hex


def test_is_protected_reflects_configuration() -> None:
    assert is_protected(None) is False
    assert is_protected(hash_credential("secret-secret")) is True


# --------------------------------------------------------------------------
# shared validation path (MQTT hot path)
# --------------------------------------------------------------------------


class _FakeSessionFactory:
    def __call__(self):  # pragma: no cover - build_item never opens a session
        raise AssertionError("validation must not touch the database")


class _Device:
    def __init__(
        self,
        device_id: str,
        cell_count: int,
        status: str = "active",
        credential_ref: str | None = None,
    ) -> None:
        self.device_id = device_id
        self.cell_count = cell_count
        self.sampling_frequency_hz = 1.0
        self.status = status
        self.credential_ref = credential_ref


def make_service(*devices) -> IngestionService:
    cache = DeviceCache()
    cache.load(devices)
    return IngestionService(_FakeSessionFactory(), cache, get_settings())


def payload(**overrides) -> dict:
    data = {
        "schema_version": 1,
        "message_id": "SEC_001-00000001",
        "device_id": "SEC_001",
        "measured_at": "2026-10-16T05:30:00Z",
        "pack_voltage": 12.15,
        "current": 1.25,
        "temperature": 29.4,
        "cell_voltages": [4.05, 4.04, 4.06],
    }
    data.update(overrides)
    return data


def test_open_device_still_ingests_without_a_credential() -> None:
    service = make_service(_Device("SEC_001", 3))
    assert isinstance(service.build_item("SEC_001", payload()), ValidatedSample)


def test_protected_device_rejects_a_payload_with_no_credential() -> None:
    service = make_service(_Device("SEC_001", 3, credential_ref=hash_credential("s3cret-key")))
    item = service.build_item("SEC_001", payload())
    assert isinstance(item, RejectionRecord)
    assert item.reason == ing.REASON_BAD_CREDENTIAL


def test_protected_device_rejects_a_wrong_credential() -> None:
    service = make_service(_Device("SEC_001", 3, credential_ref=hash_credential("s3cret-key")))
    item = service.build_item(
        "SEC_001", payload(), presented_credential="not-the-key"
    )
    assert isinstance(item, RejectionRecord)
    assert item.reason == ing.REASON_BAD_CREDENTIAL


def test_protected_device_accepts_the_header_credential() -> None:
    service = make_service(_Device("SEC_001", 3, credential_ref=hash_credential("s3cret-key")))
    item = service.build_item(
        "SEC_001", payload(), presented_credential="s3cret-key"
    )
    assert isinstance(item, ValidatedSample)


def test_payload_inline_credential_authenticates_and_is_never_stored() -> None:
    """The MQTT convention: the secret rides in the payload's ``credential`` field."""
    service = make_service(_Device("SEC_001", 3, credential_ref=hash_credential("s3cret-key")))
    item = service.build_item("SEC_001", {**payload(), "credential": "s3cret-key"})

    assert isinstance(item, ValidatedSample)
    # Stripped before validation, so it cannot leak into additional_data.
    assert item.additional_data is None or "credential" not in item.additional_data


def test_inline_credential_does_not_override_an_explicit_header() -> None:
    service = make_service(_Device("SEC_001", 3, credential_ref=hash_credential("s3cret-key")))
    item = service.build_item(
        "SEC_001",
        {**payload(), "credential": "wrong-inline"},
        presented_credential="s3cret-key",
    )
    assert isinstance(item, ValidatedSample)


def test_inline_credential_is_rejected_when_wrong() -> None:
    service = make_service(_Device("SEC_001", 3, credential_ref=hash_credential("s3cret-key")))
    item = service.build_item("SEC_001", {**payload(), "credential": "wrong-inline"})
    assert isinstance(item, RejectionRecord)
    assert item.reason == ing.REASON_BAD_CREDENTIAL


def test_credential_check_runs_before_the_status_check() -> None:
    """An unauthenticated caller must not learn whether a device is retired."""
    service = make_service(
        _Device("SEC_001", 3, status="retired", credential_ref=hash_credential("s3cret-key"))
    )
    item = service.build_item("SEC_001", payload())
    assert isinstance(item, RejectionRecord)
    assert item.reason == ing.REASON_BAD_CREDENTIAL


def test_caller_payload_dict_is_not_mutated() -> None:
    service = make_service(_Device("SEC_001", 3))
    original = payload()
    snapshot = dict(original)
    service.build_item("SEC_001", {**original, "credential": "unused"})
    assert original == snapshot


def test_device_cache_carries_the_credential_digest() -> None:
    cache = DeviceCache()
    digest = hash_credential("s3cret-key")
    cache.load([_Device("SEC_001", 3, credential_ref=digest)])
    info = cache.get("SEC_001")
    assert info is not None
    assert info.credential_ref == digest


# --------------------------------------------------------------------------
# API: registration, ingestion auth, rotation, admin key
# --------------------------------------------------------------------------


def _register_body(device_id: str, credential: str | None = None) -> dict:
    body = {
        "device_id": device_id,
        "name": "Secure pack",
        "cell_count": 3,
        "sampling_frequency_hz": 1.0,
    }
    if credential is not None:
        body["credential"] = credential
    return body


def _telemetry_body(device_id: str) -> dict:
    return {
        "schema_version": 1,
        "message_id": f"{device_id}-1",
        "device_id": device_id,
        "measured_at": "2026-10-20T08:00:00Z",
        "current": -1.5,
        "temperature": 30.0,
        "cell_voltages": [3.90, 3.91, 3.92],
        "pack_voltage": 11.73,
    }


def _stored_digest(device_id: str) -> str | None:
    from app.db.database import SessionLocal
    from app.db.models import Device

    async def fetch() -> str | None:
        from sqlalchemy import select

        async with SessionLocal() as session:
            result = await session.execute(
                select(Device.credential_ref).where(Device.device_id == device_id)
            )
            return result.scalar_one_or_none()

    return asyncio.run(fetch())


def _stored_additional_data(device_id: str) -> dict | None:
    from app.db.database import SessionLocal
    from app.db.models import TelemetrySample

    async def fetch() -> dict | None:
        from sqlalchemy import select

        async with SessionLocal() as session:
            result = await session.execute(
                select(TelemetrySample.additional_data)
                .where(TelemetrySample.device_id == device_id)
                .limit(1)
            )
            return result.scalar_one_or_none()

    return asyncio.run(fetch())


@pytest.fixture
def admin_key(monkeypatch):
    """Temporarily enable the admin key on the shared settings object."""
    settings = get_settings()
    previous = settings.admin_api_key
    monkeypatch.setattr(settings, "admin_api_key", "test-admin-key-1234")
    yield "test-admin-key-1234"
    settings.admin_api_key = previous


def test_register_with_credential_stores_only_the_digest(client, require_db) -> None:
    device_id = f"SEC_API_{uuid4().hex[:8]}"
    secret = "device-secret-abc"

    created = client.post("/api/v1/devices", json=_register_body(device_id, secret))
    assert created.status_code == 201, created.text
    body = created.json()
    # Neither the secret nor its digest may appear in a response.
    assert "credential" not in body
    assert "credential_ref" not in body

    digest = _stored_digest(device_id)
    assert digest == hash_credential(secret)
    assert secret not in (digest or "")


def test_register_without_credential_stays_open(client, require_db) -> None:
    device_id = f"SEC_OPEN_{uuid4().hex[:8]}"
    created = client.post("/api/v1/devices", json=_register_body(device_id))
    assert created.status_code == 201, created.text
    assert _stored_digest(device_id) is None


def test_http_ingestion_requires_the_credential(client, require_db) -> None:
    device_id = f"SEC_ING_{uuid4().hex[:8]}"
    secret = "device-secret-xyz"
    assert client.post(
        "/api/v1/devices", json=_register_body(device_id, secret)
    ).status_code == 201

    payload = _telemetry_body(device_id)

    missing = client.post(f"/api/v1/devices/{device_id}/telemetry", json=payload)
    assert missing.status_code == 401

    wrong = client.post(
        f"/api/v1/devices/{device_id}/telemetry",
        json=payload,
        headers={DEVICE_CREDENTIAL_HEADER: "wrong-secret-1"},
    )
    assert wrong.status_code == 401

    good = client.post(
        f"/api/v1/devices/{device_id}/telemetry",
        json=payload,
        headers={DEVICE_CREDENTIAL_HEADER: secret},
    )
    assert good.status_code == 201, good.text


def test_open_device_ingests_without_any_header(client, require_db) -> None:
    device_id = f"SEC_OPEN_ING_{uuid4().hex[:8]}"
    assert client.post(
        "/api/v1/devices", json=_register_body(device_id)
    ).status_code == 201

    response = client.post(
        f"/api/v1/devices/{device_id}/telemetry", json=_telemetry_body(device_id)
    )
    assert response.status_code == 201, response.text


def test_inline_payload_credential_is_not_persisted(client, require_db) -> None:
    device_id = f"SEC_INLINE_{uuid4().hex[:8]}"
    secret = "device-secret-inl"
    assert client.post(
        "/api/v1/devices", json=_register_body(device_id, secret)
    ).status_code == 201

    body = _telemetry_body(device_id)
    body["credential"] = secret
    response = client.post(f"/api/v1/devices/{device_id}/telemetry", json=body)
    assert response.status_code == 201, response.text

    stored = _stored_additional_data(device_id)
    assert not stored or "credential" not in stored


def test_credential_rotation_requires_the_current_secret(client, require_db) -> None:
    device_id = f"SEC_ROT_{uuid4().hex[:8]}"
    old_secret = "old-secret-1234"
    new_secret = "new-secret-5678"
    assert client.post(
        "/api/v1/devices", json=_register_body(device_id, old_secret)
    ).status_code == 201

    # Without the current secret: 401.
    no_header = client.put(
        f"/api/v1/devices/{device_id}/credential", json={"credential": new_secret}
    )
    assert no_header.status_code == 401

    wrong = client.put(
        f"/api/v1/devices/{device_id}/credential",
        json={"credential": new_secret},
        headers={DEVICE_CREDENTIAL_HEADER: "wrong-current-1"},
    )
    assert wrong.status_code == 401

    rotated = client.put(
        f"/api/v1/devices/{device_id}/credential",
        json={"credential": new_secret},
        headers={DEVICE_CREDENTIAL_HEADER: old_secret},
    )
    assert rotated.status_code == 200, rotated.text
    assert _stored_digest(device_id) == hash_credential(new_secret)

    # The old secret no longer works; the new one does.
    rejected = client.post(
        f"/api/v1/devices/{device_id}/telemetry",
        json=_telemetry_body(device_id),
        headers={DEVICE_CREDENTIAL_HEADER: old_secret},
    )
    assert rejected.status_code == 401

    accepted = client.post(
        f"/api/v1/devices/{device_id}/telemetry",
        json={**_telemetry_body(device_id), "message_id": f"{device_id}-2"},
        headers={DEVICE_CREDENTIAL_HEADER: new_secret},
    )
    assert accepted.status_code == 201, accepted.text


def test_clearing_a_credential_reopens_the_device(client, require_db) -> None:
    device_id = f"SEC_CLEAR_{uuid4().hex[:8]}"
    secret = "clearable-1234"
    assert client.post(
        "/api/v1/devices", json=_register_body(device_id, secret)
    ).status_code == 201

    cleared = client.put(
        f"/api/v1/devices/{device_id}/credential",
        json={"credential": None},
        headers={DEVICE_CREDENTIAL_HEADER: secret},
    )
    assert cleared.status_code == 200, cleared.text
    assert _stored_digest(device_id) is None

    response = client.post(
        f"/api/v1/devices/{device_id}/telemetry", json=_telemetry_body(device_id)
    )
    assert response.status_code == 201, response.text


def test_admin_key_is_required_when_configured(client, require_db, admin_key) -> None:
    device_id = f"SEC_ADMIN_{uuid4().hex[:8]}"

    without = client.post("/api/v1/devices", json=_register_body(device_id))
    assert without.status_code == 401

    wrong = client.post(
        "/api/v1/devices",
        json=_register_body(device_id),
        headers={ADMIN_API_KEY_HEADER: "not-the-key"},
    )
    assert wrong.status_code == 401

    with_key = client.post(
        "/api/v1/devices",
        json=_register_body(device_id),
        headers={ADMIN_API_KEY_HEADER: admin_key},
    )
    assert with_key.status_code == 201, with_key.text


def test_admin_key_does_not_guard_ingestion(client, require_db, admin_key) -> None:
    """Devices authenticate themselves; the admin key is for management only."""
    device_id = f"SEC_ADMIN_ING_{uuid4().hex[:8]}"
    assert client.post(
        "/api/v1/devices",
        json=_register_body(device_id),
        headers={ADMIN_API_KEY_HEADER: admin_key},
    ).status_code == 201

    response = client.post(
        f"/api/v1/devices/{device_id}/telemetry", json=_telemetry_body(device_id)
    )
    assert response.status_code == 201, response.text


def test_short_credentials_are_rejected_at_registration(client, require_db) -> None:
    device_id = f"SEC_SHORT_{uuid4().hex[:8]}"
    response = client.post(
        "/api/v1/devices", json=_register_body(device_id, "short")
    )
    assert response.status_code == 422
