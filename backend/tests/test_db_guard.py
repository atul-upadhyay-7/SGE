"""Regression tests for the dedicated-test-database guard.

The suite drops and recreates tables; these tests pin the contract that it
can only ever run against a database whose name contains ``test``.
"""

from __future__ import annotations

import os

import pytest

from db_guard import database_name, resolve_test_database_url


def test_configured_app_database_is_rewritten_to_a_test_database():
    url = resolve_test_database_url(
        {"BMS_DATABASE_URL": "postgresql+asyncpg://bms:bms@localhost:5433/bms"}
    )
    assert database_name(url) == "bms_test"


def test_query_string_is_preserved_when_rewriting():
    url = resolve_test_database_url(
        {"BMS_DATABASE_URL": "postgresql+asyncpg://bms:bms@/bms?host=/tmp/pgdata"}
    )
    assert database_name(url) == "bms_test"
    assert url.endswith("?host=/tmp/pgdata")


def test_already_test_database_is_kept():
    url = resolve_test_database_url(
        {"BMS_DATABASE_URL": "postgresql+asyncpg://bms:bms@localhost:5433/bms_test"}
    )
    assert database_name(url) == "bms_test"


def test_explicit_override_to_a_non_test_database_fails_closed():
    with pytest.raises(RuntimeError, match="dedicated test database"):
        resolve_test_database_url(
            {"BMS_TEST_DATABASE_URL": "postgresql+asyncpg://bms:bms@localhost:5433/bms"}
        )


def test_explicit_override_to_a_test_database_passes():
    url = resolve_test_database_url(
        {"BMS_TEST_DATABASE_URL": "postgresql+asyncpg://bms:bms@localhost:5433/bms_test"}
    )
    assert database_name(url) == "bms_test"


def test_this_suite_is_actually_running_against_a_test_database():
    from app.core.config import get_settings

    assert "test" in database_name(get_settings().database_url).lower()
    assert database_name(os.environ["BMS_DATABASE_URL"]) == database_name(
        get_settings().database_url
    )
