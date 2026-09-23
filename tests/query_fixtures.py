"""Helpers for the query-layer tests: build many lane games quickly.

The user's champion always plays blue (team 100) in `role`; the lane opponent
plays red in the same role; filler champions fill the other eight slots.
"""
from __future__ import annotations

from itertools import count

from laneforge import db
from tests import factories as f

ROLES = f.ROLES
FILLER_IDS = tuple(range(900, 912))

# Items used across the query tests.
LUDENS, DEATHCAP, SHADOWFLAME, ZHONYAS, VOID, BANSHEES, MORELLO, CRYPTBLOOM = (
    6655, 3089, 4645, 3157, 3135, 3102, 3165, 3137)
MERCS = 3111
SERYLDA, YOUMUU = 6694, 3142


def seed_catalogue(conn, champions: dict[int, str]) -> None:
    """Named champions plus fillers, and a small realistic item set."""
    for cid, name in champions.items():
        f.champion(conn, cid, name)
    for cid in FILLER_IDS:
        f.champion(conn, cid, f"Filler {cid}")
    f.item(conn, LUDENS, "Luden's Companion", ability_power=95, magic_pen_flat=0, gold_cost=2900)
    f.item(conn, DEATHCAP, "Rabadon's Deathcap", ability_power=130, gold_cost=3600)
    f.item(conn, SHADOWFLAME, "Shadowflame", ability_power=110, magic_pen_flat=15, gold_cost=3200)
    f.item(conn, ZHONYAS, "Zhonya's Hourglass", ability_power=105, armor=50, gold_cost=3250)
    f.item(conn, VOID, "Void Staff", ability_power=95, magic_pen_pct=0.4, gold_cost=3000)
    f.item(conn, BANSHEES, "Banshee's Veil", ability_power=105, magic_resist=40, gold_cost=3000)
    f.item(conn, MORELLO, "Morellonomicon", ability_power=75, health=350,
           applies_grievous_wounds=True, gold_cost=2950)
    f.item(conn, CRYPTBLOOM, "Cryptbloom", ability_power=70, magic_pen_pct=0.3, gold_cost=2850)
    f.item(conn, MERCS, "Mercury's Treads", is_legendary=False, is_boots=True, magic_resist=20,
           tenacity_pct=0.3, gold_cost=1250)
    f.item(conn, SERYLDA, "Serylda's Grudge", attack_damage=45, armor_pen_pct=0.3, gold_cost=3000)
    f.item(conn, YOUMUU, "Youmuu's Ghostblade", attack_damage=55, lethality=18, gold_cost=2800)


class GameMaker:
    """Inserts lane games; match ids are unique per instance prefix."""

    def __init__(self, conn, prefix: str = "NA1_"):
        self.conn = conn
        self.prefix = prefix
        self.ids = count(1)

    def games(self, champion: int, opponent: int, n: int, core: list[int], *, wins: int = 0,
              role: str = "MIDDLE", red_measures: dict | None = None,
              blue_measures: dict | None = None) -> None:
        slot = ROLES.index(role)
        fillers = [c for c in FILLER_IDS if c not in (champion, opponent)]
        blue = fillers[:4]
        red = fillers[4:8]
        blue.insert(slot, champion)
        red.insert(slot, opponent)
        measures = {}
        for number in range(1, 6):
            if blue_measures:
                measures[number] = blue_measures
        for number in range(6, 11):
            if red_measures:
                measures[number] = red_measures
        for g in range(n):
            match_id = f"{self.prefix}{next(self.ids)}"
            f.match(self.conn, match_id, blue, red, winning_team=100 if g < wins else 200,
                    measures=measures)
            if core:
                f.purchases(self.conn, match_id, slot + 1, core)

    def done(self) -> None:
        self.conn.commit()
        db.refresh_views(self.conn)
