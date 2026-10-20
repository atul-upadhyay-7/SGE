"""CORS middleware tests (frontend connectivity)."""

from __future__ import annotations

from fastapi.testclient import TestClient

API = "/api/v1"


def test_preflight_allowed_for_configured_origin(client: TestClient) -> None:
    r = client.options(
        f"{API}/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code in (200, 204)
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_simple_request_carries_allow_origin(client: TestClient) -> None:
    r = client.get(f"{API}/health", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_disallowed_origin_gets_no_allow_origin(client: TestClient) -> None:
    r = client.get(f"{API}/health", headers={"Origin": "http://evil.example"})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers


def test_preflight_allows_credential_header(client: TestClient) -> None:
    r = client.options(
        f"{API}/devices/d1/telemetry",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-device-credential",
        },
    )
    assert r.status_code in (200, 204)
    allowed = r.headers.get("access-control-allow-headers", "").lower()
    assert "x-device-credential" in allowed or allowed == "*"
