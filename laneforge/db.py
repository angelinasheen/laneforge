"""Database access. Raw SQL only (course rule: no ORM).

Every module opens connections through `connect()` and runs SQL with psycopg 3
using named parameters (`%(name)s`). Rows come back as dicts.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

REPO_ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = REPO_ROOT / "sql"

DEFAULT_DSN = "postgresql:///laneforge"
TEST_DSN = "postgresql:///laneforge_test"

SCHEMA_FILES = ("reset.sql", "schema.sql", "views.sql", "refresh.sql")


def dsn() -> str:
    """The connection string, from DATABASE_URL or the local default."""
    return os.environ.get("DATABASE_URL", DEFAULT_DSN)


def connect(conninfo: str | None = None, **kwargs) -> psycopg.Connection:
    """Open a connection whose cursors return dict rows."""
    return psycopg.connect(conninfo or dsn(), row_factory=dict_row, **kwargs)


def apply_sql_file(conn: psycopg.Connection, name: str) -> None:
    """Execute one file from sql/ as a single batch."""
    path = SQL_DIR / name
    conn.execute(path.read_text())


def rebuild_schema(conn: psycopg.Connection) -> None:
    """Drop and recreate everything, then refresh the (empty) views."""
    for name in SCHEMA_FILES:
        apply_sql_file(conn, name)
    conn.commit()


def refresh_views(conn: psycopg.Connection) -> None:
    """Rebuild the materialized views after base tables change."""
    apply_sql_file(conn, "refresh.sql")
    conn.commit()
