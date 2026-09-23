"""Shared pytest fixtures. Tests run against a real local database
(`laneforge_test`) so that constraints, triggers and materialized views are
exercised for real. Each test starts from empty tables.
"""
from __future__ import annotations

import os

import pytest

from laneforge import db

TABLES_IN_DELETE_ORDER = (
    "build_item",
    "saved_build_enemy",
    "saved_build",
    "purchase_event",
    "participant",
    "match",
    "item",
    "champion",
    "user_profile",
)


@pytest.fixture(scope="session")
def test_dsn() -> str:
    return os.environ.get("TEST_DATABASE_URL", db.TEST_DSN)


@pytest.fixture(scope="session")
def schema(test_dsn):
    """Recreate the schema once per test session."""
    with db.connect(test_dsn) as conn:
        db.rebuild_schema(conn)
    yield


@pytest.fixture
def conn(schema, test_dsn):
    """A connection to an empty database. Commits are allowed; tables are
    truncated before every test so state never leaks between tests."""
    with db.connect(test_dsn) as connection:
        connection.execute(
            "TRUNCATE " + ", ".join(TABLES_IN_DELETE_ORDER) + " RESTART IDENTITY CASCADE"
        )
        connection.commit()
        yield connection
        connection.rollback()
