"""Interactive docs are served locally but not in production."""

from __future__ import annotations

import importlib

from app.core.config import Settings, get_settings


def _build_app(monkeypatch, environment: str):
    import app.main as main

    monkeypatch.setattr(get_settings(), "environment", environment)
    try:
        return importlib.reload(main).app
    finally:
        monkeypatch.undo()
        importlib.reload(main)


def test_docs_are_served_outside_production(monkeypatch) -> None:
    app = _build_app(monkeypatch, "local")
    assert app.docs_url == "/docs" and app.openapi_url == "/openapi.json"


def test_docs_and_schema_are_not_served_in_production(monkeypatch) -> None:
    app = _build_app(monkeypatch, "production")
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None
    assert Settings(environment="production").is_production
