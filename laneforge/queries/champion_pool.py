"""What a champion actually buys at a role: the filter behind situational picks.

Draft 4 says evidence "stops Thornmail being suggested to a mage". The stat
model alone cannot do that, so once a champion has MIN_GAMES full-core games at
a role, situational candidates are limited to items that champion actually
buys at that role: completed in at least POOL_MIN_SHARE of the champion's
games there, and never fewer than POOL_MIN_GAMES games, so a single stray
purchase does not put a tank item on a mage's list. Below MIN_GAMES the pool is
unknown and nothing is dropped. (Deviation from draft 4, which only says
"never completed"; the floor is documented in docs/QUERIES.md.)
"""
from __future__ import annotations

from dataclasses import dataclass

from laneforge.queries.models import MIN_GAMES

FULL_CORE = 3
POOL_MIN_SHARE = 0.02     # an item is "bought" at a role if completed in >= 2% of games there
POOL_MIN_GAMES = 2        # ... and in at least two games regardless of share

SQL_FULL_CORE_GAMES = """
SELECT COUNT(*) AS games
FROM participant_core pc
WHERE pc.champion_id = %(champion_id)s
  AND pc.role = %(role)s
  AND pc.legendary_completions >= %(full_core)s
"""

SQL_COMPLETED_ITEMS = """
SELECT pe.item_id
FROM participant_core pc
JOIN purchase_event pe
  ON pe.match_id = pc.match_id AND pe.participant_number = pc.participant_number
WHERE pc.champion_id = %(champion_id)s
  AND pc.role = %(role)s
GROUP BY pe.item_id
HAVING COUNT(DISTINCT (pc.match_id, pc.participant_number)) >= %(min_games)s
"""


@dataclass(frozen=True)
class ItemPool:
    full_core_games: int
    completed: frozenset[int]

    @property
    def known(self) -> bool:
        """True when the sample is large enough to trust 'never bought'."""
        return self.full_core_games >= MIN_GAMES

    def allows(self, item_id: int) -> bool:
        return not self.known or item_id in self.completed


def item_pool(conn, champion_id: int, role: str) -> ItemPool:
    """One pass per request: the full-core count and the completed-item set."""
    params = {"champion_id": champion_id, "role": role, "full_core": FULL_CORE}
    games = int(conn.execute(SQL_FULL_CORE_GAMES, params).fetchone()["games"])
    if games < MIN_GAMES:
        return ItemPool(full_core_games=games, completed=frozenset())
    floor = max(POOL_MIN_GAMES, int(games * POOL_MIN_SHARE))
    rows = conn.execute(SQL_COMPLETED_ITEMS, {**params, "min_games": floor}).fetchall()
    return ItemPool(full_core_games=games, completed=frozenset(r["item_id"] for r in rows))
