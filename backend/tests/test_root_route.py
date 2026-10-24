"""The base URL identifies the service instead of returning a bare 404."""

from __future__ import annotations


def test_root_route_identifies_service(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["api"] == "/api/v1"
    assert body["health"] == "/api/v1/health"
    assert body["version"]
