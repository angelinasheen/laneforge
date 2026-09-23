"""Draft 4 "Saved builds": create, list, edit, delete. Constraints 1, 3, 8, 11.

Every write validates first, then runs inside one transaction; database
constraint and trigger errors are translated into ValidationError.
`with conn.transaction()` followed by `conn.commit()` commits whether or not
the caller's connection already had an implicit transaction open.
"""
from __future__ import annotations

from contextlib import contextmanager

import psycopg.errors

from laneforge.queries._rows import (
    ITEM_NUMERIC_FIELDS, load_champions, load_items, user_from_row,
)
from laneforge.queries.builds import FULL_CORE, build_row
from laneforge.queries.errors import ValidationError
from laneforge.queries.models import BuildRow, ItemRef, SavedBuild
from laneforge.queries.saved_sql import (
    SQL_BUILD_ENEMIES, SQL_BUILD_ITEMS, SQL_DELETE_BUILD, SQL_DELETE_BUILD_ITEMS,
    SQL_GET_BUILD, SQL_INSERT_BUILD, SQL_INSERT_BUILD_ITEM, SQL_INSERT_ENEMY, SQL_LIST_BUILDS,
    SQL_MARK_CUSTOMIZED, SQL_OBSERVED_ROWS, SQL_OBSERVED_SEQUENCE, SQL_RENAME_BUILD, SQL_USER_EXISTS,
)
from laneforge.queries.validate import (
    UNKNOWN_CHAMPION_MESSAGE, require_distinct_matchup, require_enemies, require_role,
)

BUILD_SIZE = 3
NAME_MAX = 60
NOTES_MAX = 2000
DB_ERRORS = (psycopg.errors.CheckViolation, psycopg.errors.UniqueViolation,
             psycopg.errors.ForeignKeyViolation, psycopg.errors.NotNullViolation)


@contextmanager
def _write(conn):
    """One transaction; DB constraint errors surface as ValidationError."""
    try:
        with conn.transaction():
            yield
    except DB_ERRORS as exc:
        message = exc.diag.message_primary or "the build violates a database constraint"
        raise ValidationError(message[0].upper() + message[1:] + ".") from exc
    conn.commit()


def clean_name(name: str | None) -> str:
    cleaned = (name or "").strip()
    if not 1 <= len(cleaned) <= NAME_MAX:
        raise ValidationError(f"Build name must be 1 to {NAME_MAX} characters.")
    return cleaned


def clean_notes(notes: str | None) -> str | None:
    cleaned = (notes or "").strip()
    if len(cleaned) > NOTES_MAX:
        raise ValidationError(f"Notes can be at most {NOTES_MAX:,} characters.")
    return cleaned or None


def require_build_items(conn, item_ids) -> tuple[int, ...]:
    """Exactly three distinct, existing, legendary non-boots items (constraints 1, 8)."""
    ids = tuple(item_ids)
    if len(ids) != BUILD_SIZE:
        raise ValidationError(f"A build holds exactly {BUILD_SIZE} items.")
    if len(set(ids)) != BUILD_SIZE:
        raise ValidationError("A build cannot contain the same item twice.")
    items = load_items(conn, ids)
    for item_id in ids:
        item = items.get(item_id)
        if item is None:
            raise ValidationError(f"Unknown item id {item_id}.")
        if not item.is_legendary or item.is_boots:
            raise ValidationError(f"{item.name} is not a legendary item; builds hold "
                                  f"legendary items only.")
    return ids


def _require_champions(conn, ids: tuple[int, ...]) -> None:
    found = load_champions(conn, ids)
    missing = [i for i in ids if i not in found]
    if missing:
        raise ValidationError(UNKNOWN_CHAMPION_MESSAGE)


def is_observed_sequence(conn, champion_id: int, role: str, item_ids) -> bool:
    ids = tuple(item_ids)
    if len(ids) != BUILD_SIZE:
        return False
    row = conn.execute(SQL_OBSERVED_SEQUENCE, {
        "champion_id": champion_id, "role": role,
        "item1": ids[0], "item2": ids[1], "item3": ids[2],
    }).fetchone()
    return row is not None


def create_build(conn, user_id: int, *, name: str, notes: str | None, champion_id: int,
                 role: str, opponent_champion_id: int, enemy_champion_ids, item_ids,
                 observed: bool) -> int:
    name, notes = clean_name(name), clean_notes(notes)
    require_role(role)
    require_distinct_matchup(champion_id, opponent_champion_id)
    enemies = require_enemies(champion_id, opponent_champion_id, enemy_champion_ids)
    items = require_build_items(conn, item_ids)
    if conn.execute(SQL_USER_EXISTS, {"user_id": user_id}).fetchone() is None:
        raise ValidationError("Sign in again: that account no longer exists.")
    _require_champions(conn, (champion_id, opponent_champion_id, *enemies))
    # Constraint 11: only a sequence seen in match data may be marked observed.
    customized = not (observed and is_observed_sequence(conn, champion_id, role, items))
    with _write(conn):
        build_id = conn.execute(SQL_INSERT_BUILD, {
            "user_id": user_id, "champion_id": champion_id,
            "opponent_champion_id": opponent_champion_id, "name": name, "notes": notes,
            "role": role, "is_customized": customized,
        }).fetchone()["build_id"]
        _insert_children(conn, build_id, enemies, items)
    return build_id


def _insert_children(conn, build_id: int, enemies, items) -> None:
    with conn.cursor() as cur:
        if enemies:
            cur.executemany(SQL_INSERT_ENEMY,
                            [{"build_id": build_id, "champion_id": c} for c in enemies])
        cur.executemany(SQL_INSERT_BUILD_ITEM,
                        [{"build_id": build_id, "item_id": i, "position": p}
                         for p, i in enumerate(items, start=1)])


def update_items(conn, build_id: int, item_ids) -> None:
    items = require_build_items(conn, item_ids)
    with _write(conn):
        if conn.execute(SQL_MARK_CUSTOMIZED, {"build_id": build_id}).rowcount == 0:
            raise ValidationError("That build no longer exists.")
        conn.execute(SQL_DELETE_BUILD_ITEMS, {"build_id": build_id})
        _insert_children(conn, build_id, (), items)


def rename_build(conn, build_id: int, name: str, notes: str | None) -> None:
    params = {"build_id": build_id, "name": clean_name(name), "notes": clean_notes(notes)}
    with _write(conn):
        if conn.execute(SQL_RENAME_BUILD, params).rowcount == 0:
            raise ValidationError("That build no longer exists.")


def delete_build(conn, build_id: int) -> None:
    with _write(conn):
        if conn.execute(SQL_DELETE_BUILD, {"build_id": build_id}).rowcount == 0:
            raise ValidationError("That build no longer exists.")


def stat_totals(items: tuple[ItemRef, ...]) -> dict[str, float]:
    return {f: sum(getattr(i, f) for i in items) for f in ITEM_NUMERIC_FIELDS}


def _group(rows: list[dict], key: str = "build_id") -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r[key], []).append(r)
    return grouped


def _assemble(conn, rows: list[dict]) -> list[SavedBuild]:
    if not rows:
        return []
    ids = [r["build_id"] for r in rows]
    enemy_rows = _group(conn.execute(SQL_BUILD_ENEMIES, {"ids": ids}).fetchall())
    item_rows = _group(conn.execute(SQL_BUILD_ITEMS, {"ids": ids}).fetchall())
    champion_ids = [r["champion_id"] for r in rows] + [r["opponent_champion_id"] for r in rows]
    champion_ids += [e["champion_id"] for es in enemy_rows.values() for e in es]
    champions = load_champions(conn, champion_ids)
    items = load_items(conn, [x["item_id"] for xs in item_rows.values() for x in xs])
    observed = _observed_counts(conn, ids)
    return [_saved_build(r, champions, items, enemy_rows.get(r["build_id"], []),
                         item_rows.get(r["build_id"], []), observed.get(r["build_id"]))
            for r in rows]


def _observed_counts(conn, build_ids: list[int]) -> dict[int, dict]:
    """build_id -> games/wins/sample_size, only for uncustomized builds seen in their matchup."""
    rows = conn.execute(SQL_OBSERVED_ROWS, {"ids": build_ids, "full_core": FULL_CORE}).fetchall()
    return {r["build_id"]: r for r in rows if r["games"] > 0}


def _observed_row(build_items: tuple[ItemRef, ...], counts: dict | None) -> BuildRow | None:
    if counts is None:
        return None
    return build_row(build_items, int(counts["games"]), int(counts["wins"]),
                     int(counts["sample_size"]))


def _saved_build(r, champions, items, enemy_rows, item_rows, counts) -> SavedBuild:
    build_items = tuple(items[x["item_id"]] for x in item_rows)
    return SavedBuild(
        build_id=r["build_id"], user=user_from_row(r), name=r["name"], notes=r["notes"],
        champion=champions[r["champion_id"]], role=r["role"],
        opponent=champions[r["opponent_champion_id"]],
        enemies=tuple(champions[e["champion_id"]] for e in enemy_rows),
        items=build_items, is_customized=r["is_customized"], created_at=r["build_created_at"],
        stat_totals=stat_totals(build_items),
        observed=_observed_row(build_items, counts),
    )


def list_builds(conn, user_id: int) -> list[SavedBuild]:
    return _assemble(conn, conn.execute(SQL_LIST_BUILDS, {"user_id": user_id}).fetchall())


def get_build(conn, build_id: int) -> SavedBuild | None:
    built = _assemble(conn, conn.execute(SQL_GET_BUILD, {"build_id": build_id}).fetchall())
    return built[0] if built else None
