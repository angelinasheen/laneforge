"""Row -> dataclass mapping and batch loaders for the reference entities.

Every page resolves champion and item ids in one round-trip with
`= ANY(%(ids)s)`, never one query per row.
"""
from __future__ import annotations

from dataclasses import fields
from decimal import Decimal
from typing import Iterable, TypeVar

from laneforge.queries.models import ChampionRef, ItemRef, UserRef

T = TypeVar("T")

CHAMPION_COLUMNS = ", ".join(f.name for f in fields(ChampionRef))
ITEM_COLUMNS = ", ".join(f.name for f in fields(ItemRef))

SQL_CHAMPIONS_BY_IDS = f"SELECT {CHAMPION_COLUMNS} FROM champion WHERE champion_id = ANY(%(ids)s)"
SQL_ITEMS_BY_IDS = f"SELECT {ITEM_COLUMNS} FROM item WHERE item_id = ANY(%(ids)s)"

# Numeric ItemRef fields: the stat line (plus gold cost) summed on the build page.
ITEM_NUMERIC_FIELDS: tuple[str, ...] = tuple(
    f.name for f in fields(ItemRef)
    if f.name not in ("item_id", "name", "is_legendary", "is_boots", "applies_grievous_wounds")
)


def _plain(value):
    return float(value) if isinstance(value, Decimal) else value


def build(cls: type[T], row: dict) -> T:
    """Construct `cls` from the row's columns that match its field names."""
    return cls(**{f.name: _plain(row[f.name]) for f in fields(cls) if f.name in row})


def champion_from_row(row: dict) -> ChampionRef:
    return build(ChampionRef, row)


def item_from_row(row: dict) -> ItemRef:
    return build(ItemRef, row)


def user_from_row(row: dict) -> UserRef:
    return UserRef(user_id=row["user_id"], display_name=row["display_name"],
                   created_at=row["created_at"])


def _distinct_ids(ids: Iterable[int | None]) -> list[int]:
    return sorted({i for i in ids if i is not None})


def load_champions(conn, ids: Iterable[int | None]) -> dict[int, ChampionRef]:
    wanted = _distinct_ids(ids)
    if not wanted:
        return {}
    rows = conn.execute(SQL_CHAMPIONS_BY_IDS, {"ids": wanted}).fetchall()
    return {r["champion_id"]: champion_from_row(r) for r in rows}


def load_items(conn, ids: Iterable[int | None]) -> dict[int, ItemRef]:
    wanted = _distinct_ids(ids)
    if not wanted:
        return {}
    rows = conn.execute(SQL_ITEMS_BY_IDS, {"ids": wanted}).fetchall()
    return {r["item_id"]: item_from_row(r) for r in rows}


def rate(wins: int, games: int) -> float:
    return wins / games if games else 0.0
