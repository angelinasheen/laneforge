"""Small helpers that insert realistic rows for tests. Keyword arguments
override any column; unspecified stats default to zero so a test only names
what it cares about.
"""
from __future__ import annotations

from datetime import datetime

ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")

CHAMPION_DEFAULTS = dict(
    base_health=600, health_per_level=100, base_armor=30, armor_per_level=4,
    base_magic_resist=30, magic_resist_per_level=1.5, base_attack_damage=60,
    attack_damage_per_level=0, base_attack_speed=0.650, attack_speed_per_level_pct=2.5,
)

ITEM_DEFAULTS = dict(
    gold_cost=3000, is_legendary=True, is_boots=False,
    attack_damage=0, ability_power=0, health=0, mana=0, attack_speed_pct=0,
    crit_chance_pct=0, move_speed=0, life_steal_pct=0, armor=0, magic_resist=0,
    ability_haste=0, lethality=0, armor_pen_pct=0, magic_pen_flat=0, magic_pen_pct=0,
    tenacity_pct=0, applies_grievous_wounds=False,
)


def _insert(conn, table: str, row: dict) -> None:
    cols = ", ".join(row)
    params = ", ".join(f"%({c})s" for c in row)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({params})", row)


def champion(conn, champion_id: int, name: str, **overrides) -> int:
    row = dict(CHAMPION_DEFAULTS, champion_id=champion_id, name=name,
               ddragon_key=overrides.pop("ddragon_key", name.replace(" ", "")))
    row.update(overrides)
    _insert(conn, "champion", row)
    return champion_id


def item(conn, item_id: int, name: str, **overrides) -> int:
    row = dict(ITEM_DEFAULTS, item_id=item_id, name=name)
    row.update(overrides)
    _insert(conn, "item", row)
    return item_id


def user(conn, display_name: str) -> int:
    row = conn.execute(
        "INSERT INTO user_profile (display_name) VALUES (%(n)s) RETURNING user_id",
        {"n": display_name},
    ).fetchone()
    return row["user_id"]


def match(conn, match_id: str, blue: list[int], red: list[int], *,
          winning_team: int = 100, duration_seconds: int = 1800,
          game_version: str = "16.18.712.1234", seed_tier: str = "GOLD",
          start_time: datetime | None = None, measures: dict | None = None) -> str:
    """Insert a match with ten participants. `blue` and `red` are five
    champion ids in role order TOP, JUNGLE, MIDDLE, BOTTOM, UTILITY.
    Participant numbers 1-5 are blue (team 100), 6-10 are red (team 200).
    `measures` maps participant_number -> dict of the five per-game measures."""
    assert len(blue) == 5 and len(red) == 5
    _insert(conn, "match", dict(
        match_id=match_id, game_version=game_version,
        start_time=start_time or datetime(2026, 9, 15, 12, 0, 0),
        duration_seconds=duration_seconds, winning_team=winning_team, seed_tier=seed_tier,
    ))
    measures = measures or {}
    number = 1
    for team, roster in ((100, blue), (200, red)):
        for role, champion_id in zip(ROLES, roster):
            m = dict(physical_damage=10000, magic_damage=10000, true_damage=2000,
                     healing_done=3000, cc_seconds=20)
            m.update(measures.get(number, {}))
            _insert(conn, "participant", dict(
                match_id=match_id, participant_number=number, team=team, role=role,
                champion_id=champion_id, **m,
            ))
            number += 1
    return match_id


def purchases(conn, match_id: str, participant_number: int, item_ids: list[int],
              *, start_ms: int = 600_000, step_ms: int = 240_000) -> None:
    """Insert completed-item purchase events in the given order, spaced evenly."""
    for n, item_id in enumerate(item_ids, start=1):
        _insert(conn, "purchase_event", dict(
            match_id=match_id, participant_number=participant_number,
            event_number=n, game_time_ms=start_ms + (n - 1) * step_ms, item_id=item_id,
        ))
