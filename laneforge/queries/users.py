"""Display-name accounts (no passwords: the course app only needs identity)."""
from __future__ import annotations

from laneforge.queries._rows import user_from_row
from laneforge.queries.errors import ValidationError
from laneforge.queries.models import UserRef

NAME_MIN = 2
NAME_MAX = 40

# The no-op update makes RETURNING yield the row whether it was inserted or existed.
SQL_UPSERT_USER = """
INSERT INTO user_profile (display_name) VALUES (%(display_name)s)
ON CONFLICT (display_name) DO UPDATE SET display_name = EXCLUDED.display_name
RETURNING user_id, display_name, created_at
"""
SQL_GET_USER = "SELECT user_id, display_name, created_at FROM user_profile WHERE user_id = %(user_id)s"


def clean_display_name(display_name: str | None) -> str:
    name = (display_name or "").strip()
    if not NAME_MIN <= len(name) <= NAME_MAX:
        raise ValidationError(f"Display name must be {NAME_MIN} to {NAME_MAX} characters.")
    return name


def get_or_create_user(conn, display_name: str) -> UserRef:
    name = clean_display_name(display_name)
    with conn.transaction():
        row = conn.execute(SQL_UPSERT_USER, {"display_name": name}).fetchone()
    conn.commit()
    return user_from_row(row)


def get_user(conn, user_id: int) -> UserRef | None:
    row = conn.execute(SQL_GET_USER, {"user_id": user_id}).fetchone()
    return user_from_row(row) if row else None
