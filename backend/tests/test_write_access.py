"""Write access control for dashboard actions (operator key).

Every state-changing route must be guarded by exactly one of: the per-device
ingest credential, the admin key or the operator key. The route inventory test
fails when a new unguarded write endpoint is added.
"""

from __future__ import annotations

import pytest

from app.api.deps import require_operator_key
from app.api.devices import require_admin_key
from app.core.config import Settings, get_settings
from app.core.security import OPERATOR_API_KEY_HEADER
from app.main import app

OPERATOR_KEY = "test-operator-key-1234"

# Device telemetry ingestion authenticates with the per-device credential.
DEVICE_CREDENTIAL_ROUTES = {("POST", "/api/v1/devices/{device_id}/telemetry")}


def _write_routes():
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        for method in methods & {"POST", "PUT", "PATCH", "DELETE"}:
            yield method, route.path, route


def test_every_write_route_has_an_explicit_guard() -> None:
    unguarded = []
    for method, path, route in _write_routes():
        calls = {dep.call for dep in route.dependant.dependencies}
        if (method, path) in DEVICE_CREDENTIAL_ROUTES:
            continue
        if require_admin_key in calls or require_operator_key in calls:
            continue
        unguarded.append(f"{method} {path}")
    assert not unguarded, f"write routes without a guard: {unguarded}"


def test_production_refuses_to_start_without_both_keys() -> None:
    assert Settings(environment="production").access_control_problems() == [
        "BMS_ADMIN_API_KEY is not set",
        "BMS_OPERATOR_API_KEY is not set",
        "BMS_VIEWER_API_KEY is not set",
    ]
    assert Settings(
        environment="production",
        admin_api_key="a",
        operator_api_key="b",
        viewer_api_key="c",
    ).access_control_problems() == []
    assert Settings(environment="local").access_control_problems() == []


@pytest.fixture()
def operator_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "operator_api_key", OPERATOR_KEY)
    return OPERATOR_KEY


GUARDED_CALLS = [
    ("post", "/api/v1/reports", {"json": {}}),
    ("post", "/api/v1/alerts/1/ack", {"json": {"acked_by": "tester"}}),
    ("post", "/api/v1/anomalies/1/acknowledge", {"json": {"actor": "tester"}}),
    ("post", "/api/v1/anomalies/1/resolve", {"json": {"actor": "tester", "note": "x"}}),
    ("post", "/api/v1/devices/NOPE/cycles/aggregate", {}),
    ("post", "/api/v1/devices/NOPE/alerts/evaluate", {}),
]


@pytest.mark.parametrize("method,path,kwargs", GUARDED_CALLS)
def test_write_actions_need_the_operator_key(
    client, require_db, operator_key, method, path, kwargs
) -> None:
    missing = getattr(client, method)(path, **kwargs)
    wrong = getattr(client, method)(
        path, headers={OPERATOR_API_KEY_HEADER: "wrong"}, **kwargs
    )
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert OPERATOR_KEY not in missing.text + wrong.text
    ok = getattr(client, method)(
        path, headers={OPERATOR_API_KEY_HEADER: OPERATOR_KEY}, **kwargs
    )
    assert ok.status_code != 401  # reaches the handler (404/422/201 are fine)


def test_reads_stay_open_and_health_reports_protection(client, operator_key) -> None:
    body = client.get("/api/v1/health").json()
    assert body["access_control"]["operator_key"] is True
    assert OPERATOR_KEY not in str(body)
    assert client.get("/api/v1/alerts").status_code in {200, 503}


def test_unset_key_keeps_local_development_open(client, require_db) -> None:
    assert get_settings().operator_api_key is None
    assert client.post("/api/v1/reports", json={}).status_code == 201
