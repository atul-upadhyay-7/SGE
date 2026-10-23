"""Read access control (viewer key). Reads are guarded application-wide."""

from __future__ import annotations

import re

import pytest
from app.core.config import get_settings
from app.core.security import (
    ADMIN_API_KEY_HEADER,
    OPERATOR_API_KEY_HEADER,
    VIEWER_API_KEY_HEADER,
)
from app.main import app

VIEWER = "test-viewer-key-1234"
API = "/api/v1"


@pytest.fixture()
def viewer_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "viewer_api_key", VIEWER)
    return VIEWER


def _iter_routes(routes, prefix=""):
    """Yield ``(full_path, route)`` for every concrete route.

    FastAPI >= 0.161 wraps each included APIRouter in an object in
    ``app.routes`` instead of flattening its routes in, so a flat walk
    finds nothing — and this inventory silently came back empty. Recurse
    through ``original_router`` and apply the include prefix so the walk
    works on both route-tree shapes.
    """
    for route in routes:
        included = getattr(route, "original_router", None)
        if included is not None:
            include_prefix = (
                getattr(getattr(route, "include_context", None), "prefix", "")
                or ""
            )
            yield from _iter_routes(included.routes, prefix + include_prefix)
        else:
            yield prefix + (getattr(route, "path", "") or ""), route


def _get_paths() -> list[str]:
    paths = []
    for path, route in _iter_routes(app.routes):
        if "GET" in (getattr(route, "methods", None) or set()):
            if path.startswith(API):
                paths.append(re.sub(r"\{[^}]+\}", "1", path))
    return sorted(set(paths))


def test_every_api_get_route_is_found() -> None:
    paths = _get_paths()
    assert f"{API}/health" in paths
    assert f"{API}/stream" in paths
    assert len(paths) > 20


def test_every_read_except_liveness_needs_a_key(client, viewer_key) -> None:
    open_paths = []
    for path in _get_paths():
        if path.endswith("/stream"):
            # Streaming endpoint: check the refusal without opening the stream.
            response = client.get(path)
        else:
            response = client.get(path)
        if response.status_code != 401:
            open_paths.append((path, response.status_code))
    assert open_paths == [(f"{API}/health", 200)]


def test_viewer_operator_and_admin_keys_grant_read(client, viewer_key, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "operator_api_key", "op-key-1234567")
    monkeypatch.setattr(settings, "admin_api_key", "admin-key-1234567")
    assert client.get(f"{API}/devices").status_code == 401
    assert client.get(f"{API}/devices", headers={VIEWER_API_KEY_HEADER: "nope"}).status_code == 401
    for header, value in [
        (VIEWER_API_KEY_HEADER, VIEWER),
        (OPERATOR_API_KEY_HEADER, "op-key-1234567"),
        (ADMIN_API_KEY_HEADER, "admin-key-1234567"),
    ]:
        assert client.get(f"{API}/devices", headers={header: value}).status_code == 200


def test_refusal_does_not_echo_keys(client, viewer_key) -> None:
    body = client.get(f"{API}/devices", headers={VIEWER_API_KEY_HEADER: "guess"}).text
    assert VIEWER not in body and "guess" not in body


def test_device_ingestion_is_not_blocked_by_the_viewer_key(client, viewer_key) -> None:
    # Ingest authenticates devices itself; an unknown device is a 404, not 401.
    response = client.post(f"{API}/devices/NO_SUCH/telemetry", json={})
    assert response.status_code != 401


def test_reads_stay_open_without_a_viewer_key(client, require_db) -> None:
    assert get_settings().viewer_api_key is None
    assert client.get(f"{API}/devices").status_code == 200
