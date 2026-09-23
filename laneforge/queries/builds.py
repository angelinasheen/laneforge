"""Draft 4 "Core builds": the four-level ladder.

Level 1  matchup,  ordered three-item sequence
Level 2  matchup,  per item among the first three
Level 3  champion+role across all opponents, sequence
Level 4  champion+role across all opponents, per item

The page answers at the first level whose top row has >= MIN_GAMES games. The
denominator (sample size) at every level is the number of participants at
that scope with a full core (legendary_completions >= 3). Rows are ranked by
pick rate (equivalently games), never by win rate.
"""
from __future__ import annotations

from dataclasses import dataclass

from laneforge.queries._rows import load_champions, load_items, rate
from laneforge.queries.errors import ValidationError
from laneforge.queries.models import (
    MAX_ROWS_PER_LEVEL, MIN_GAMES, ROLE_LABELS, ROLES, BuildRow, ChampionRef, ItemRef, LadderAnswer,
)
from laneforge.queries.stats import wilson_interval
from laneforge.queries.validate import ROLE_MESSAGE, UNKNOWN_CHAMPION_MESSAGE

FULL_CORE = 3

# Level 1: sequences in the matchup. The window total is taken before LIMIT, so
# every row carries the scope's sample size (each full-core participant has
# exactly one sequence).
SQL_MATCHUP_SEQUENCES = """
SELECT pc.item1, pc.item2, pc.item3,
       COUNT(*)                                  AS games,
       SUM(CASE WHEN pc.won THEN 1 ELSE 0 END)   AS wins,
       SUM(COUNT(*)) OVER ()                     AS sample_size
FROM participant_core pc
WHERE pc.champion_id = %(champion_id)s
  AND pc.role = %(role)s
  AND pc.opponent_champion_id = %(opponent_id)s
  AND pc.legendary_completions >= %(full_core)s
GROUP BY pc.item1, pc.item2, pc.item3
ORDER BY games DESC, pc.item1, pc.item2, pc.item3
LIMIT %(max_rows)s
"""

# Level 2: per item. A participant counts once per distinct item among its
# first three (DISTINCT guards a re-bought legendary).
SQL_MATCHUP_ITEMS = """
SELECT u.item_id,
       COUNT(*)                                  AS games,
       SUM(CASE WHEN pc.won THEN 1 ELSE 0 END)   AS wins
FROM participant_core pc
CROSS JOIN LATERAL (
  SELECT DISTINCT x AS item_id FROM unnest(ARRAY[pc.item1, pc.item2, pc.item3]) AS x
) u
WHERE pc.champion_id = %(champion_id)s
  AND pc.role = %(role)s
  AND pc.opponent_champion_id = %(opponent_id)s
  AND pc.legendary_completions >= %(full_core)s
GROUP BY u.item_id
ORDER BY games DESC, u.item_id
LIMIT %(max_rows)s
"""

# Levels 3 and 4: the same queries without the opponent filter.
SQL_CHAMPION_SEQUENCES = """
SELECT pc.item1, pc.item2, pc.item3,
       COUNT(*)                                  AS games,
       SUM(CASE WHEN pc.won THEN 1 ELSE 0 END)   AS wins,
       SUM(COUNT(*)) OVER ()                     AS sample_size
FROM participant_core pc
WHERE pc.champion_id = %(champion_id)s
  AND pc.role = %(role)s
  AND pc.legendary_completions >= %(full_core)s
GROUP BY pc.item1, pc.item2, pc.item3
ORDER BY games DESC, pc.item1, pc.item2, pc.item3
LIMIT %(max_rows)s
"""

SQL_CHAMPION_ITEMS = """
SELECT u.item_id,
       COUNT(*)                                  AS games,
       SUM(CASE WHEN pc.won THEN 1 ELSE 0 END)   AS wins
FROM participant_core pc
CROSS JOIN LATERAL (
  SELECT DISTINCT x AS item_id FROM unnest(ARRAY[pc.item1, pc.item2, pc.item3]) AS x
) u
WHERE pc.champion_id = %(champion_id)s
  AND pc.role = %(role)s
  AND pc.legendary_completions >= %(full_core)s
GROUP BY u.item_id
ORDER BY games DESC, u.item_id
LIMIT %(max_rows)s
"""


@dataclass(frozen=True)
class _Level:
    level: int
    scope: str
    granularity: str
    rows: tuple[dict, ...]
    sample_size: int

    @property
    def answers(self) -> bool:
        return bool(self.rows) and self.rows[0]["games"] >= MIN_GAMES


def _sequence_level(conn, level: int, scope: str, sql: str, params: dict) -> _Level:
    rows = tuple(conn.execute(sql, params).fetchall())
    sample = int(rows[0]["sample_size"]) if rows else 0
    return _Level(level, scope, "sequence", rows, sample)


def _item_level(conn, level: int, scope: str, sql: str, params: dict, sample: int) -> _Level:
    rows = tuple(conn.execute(sql, params).fetchall())
    return _Level(level, scope, "item", rows, sample)


def _levels(conn, params: dict):
    """Yield levels lazily so the ladder stops querying once one answers."""
    matchup_seq = _sequence_level(conn, 1, "matchup", SQL_MATCHUP_SEQUENCES, params)
    yield matchup_seq
    yield _item_level(conn, 2, "matchup", SQL_MATCHUP_ITEMS, params, matchup_seq.sample_size)
    champion_seq = _sequence_level(conn, 3, "champion", SQL_CHAMPION_SEQUENCES, params)
    yield champion_seq
    yield _item_level(conn, 4, "champion", SQL_CHAMPION_ITEMS, params, champion_seq.sample_size)


def _item_ids(row: dict, granularity: str) -> tuple[int, ...]:
    if granularity == "sequence":
        return (row["item1"], row["item2"], row["item3"])
    return (row["item_id"],)


def build_row(items: tuple[ItemRef, ...], games: int, wins: int, sample_size: int) -> BuildRow:
    """One ladder row: pick rate over the scope's full-core sample, Wilson interval."""
    low, high = wilson_interval(wins, games)
    return BuildRow(items=items, games=games, wins=wins, pick_rate=rate(games, sample_size),
                    win_rate=rate(wins, games), ci_low=low, ci_high=high,
                    sufficient=games >= MIN_GAMES)


def _build_rows(conn, level: _Level) -> tuple[BuildRow, ...]:
    ids = [i for r in level.rows for i in _item_ids(r, level.granularity)]
    items = load_items(conn, ids)
    return tuple(
        build_row(tuple(items[i] for i in _item_ids(r, level.granularity)),
                  int(r["games"]), int(r["wins"]), level.sample_size)
        for r in level.rows
    )


def _label(level: _Level, opponent: ChampionRef, role: str) -> str:
    if level.scope == "matchup":
        return f"vs {opponent.name}, {ROLE_LABELS[role].lower()}: {level.sample_size:,} games"
    return f"all opponents: {level.sample_size:,} games"


def _fallback_note(level: _Level, champion: ChampionRef, opponent: ChampionRef,
                   role: str, answered: bool) -> str | None:
    who = f"{champion.name}, {ROLE_LABELS[role].lower()}"
    matchup = f"{champion.name} vs {opponent.name}"
    if not answered:
        return _unanswered_note(level, champion, role, who)
    if level.level == 2:
        return (f"Not enough {matchup} games for full three-item sequences; "
                f"showing how often each item is among the first three")
    if level.level == 3:
        return f"Not enough {matchup} games; showing {who}, all opponents"
    if level.level == 4:
        return (f"Not enough {matchup} games, and no single sequence is common enough; "
                f"showing per-item shares for {who}, all opponents")
    return None


def _unanswered_note(level: _Level, champion: ChampionRef, role: str, who: str) -> str:
    if level.sample_size == 0:
        return f"No {champion.name} games at {ROLE_LABELS[role].lower()} in this dataset"
    return (f"No build reaches {MIN_GAMES} games at any level; "
            f"showing what exists for {who}, all opponents")


def _require_champions(conn, champion_id: int, opponent_id: int) -> tuple[ChampionRef, ChampionRef]:
    refs = load_champions(conn, (champion_id, opponent_id))
    if champion_id not in refs or opponent_id not in refs:
        raise ValidationError(UNKNOWN_CHAMPION_MESSAGE)
    return refs[champion_id], refs[opponent_id]


def core_builds(conn, champion_id: int, role: str, opponent_champion_id: int) -> LadderAnswer:
    if role not in ROLES:
        raise ValidationError(ROLE_MESSAGE)
    champion, opponent = _require_champions(conn, champion_id, opponent_champion_id)
    params = {"champion_id": champion_id, "role": role, "opponent_id": opponent_champion_id,
              "full_core": FULL_CORE, "max_rows": MAX_ROWS_PER_LEVEL}
    chosen = None
    for level in _levels(conn, params):
        chosen = level
        if level.answers:
            break
    answered = chosen.answers
    return LadderAnswer(
        level=chosen.level, scope=chosen.scope, granularity=chosen.granularity,
        sample_size=chosen.sample_size, label=_label(chosen, opponent, role),
        fell_back=chosen.level > 1,
        fallback_note=_fallback_note(chosen, champion, opponent, role, answered),
        rows=_build_rows(conn, chosen),
        answered=answered,
    )
