"""Health and connectivity endpoint tests (FR-08)."""

from __future__ import annotations

from fastapi.testclient import TestClient

API = "/api/v1"


def test_liveness_returns_ok(client: TestClient) -> None:
    r = client.get(f"{API}/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["app"]
    assert body["version"]


def test_health_is_under_api_prefix(client: TestClient) -> None:
    # The un-prefixed path must not exist; FR-08 puts everything under /api/v1.
    assert client.get("/health").status_code == 404


def test_mqtt_health_reports_disabled_manager(client: TestClient) -> None:
    r = client.get("/api/v1/health/mqtt")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["connected"] is False
    assert body["topic"] == "bms/+/telemetry"
    assert "messages_received" in body


def test_database_health_returns_a_verdict(client: TestClient) -> None:
    r = client.get("/api/v1/health/database")
    assert r.status_code in (200, 503)
    body = r.json()
    if r.status_code == 200:
        assert body["status"] == "ok"
        assert body["database"] == "reachable"
    else:
        assert body["status"] == "error"


def test_openapi_lists_health_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/api/v1/health", "/api/v1/health/database", "/api/v1/health/mqtt"):
        assert path in paths
