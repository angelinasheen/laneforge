"""Evidence lines for situational items (draft 4): one comp feature at a time.

Per-item: participants on (champion, role) whose historical enemy comp met the
rule's condition and who completed the item. Class-level: the same condition,
using participant_core's completed_* flags. Only shown at >= MIN_GAMES games.
"""
from __future__ import annotations

from dataclasses import dataclass

from laneforge.queries._rows import rate
from laneforge.queries.models import MIN_GAMES, REFERENCE_LEVEL, ChampionRef, CompProfile, Evidence
from laneforge.queries.rules import (
    MAGIC_SHARE_MIN, PEN_RESIST_MIN, PHYSICAL_SHARE_MIN, condition_text,
)
from laneforge.queries.statmodel import MAGIC
from laneforge.queries.stats import wilson_interval

CLASS_RULES = ("magic", "physical", "healing", "cc")

# The rule key picks one branch of the CASE; column names stay constants.
SQL_ITEM_EVIDENCE = """
SELECT x.item_id,
       COUNT(*)                                 AS games,
       SUM(CASE WHEN x.won THEN 1 ELSE 0 END)   AS wins
FROM (
  SELECT DISTINCT pc.match_id, pc.participant_number, pc.won, pe.item_id
  FROM participant_core pc
  JOIN purchase_event pe
    ON pe.match_id = pc.match_id AND pe.participant_number = pc.participant_number
  WHERE pc.champion_id = %(champion_id)s
    AND pc.role = %(role)s
    AND pe.item_id = ANY(%(item_ids)s)
    AND CASE %(rule)s
          WHEN 'magic'    THEN pc.enemy_magic_share    >= %(magic_share_min)s
          WHEN 'physical' THEN pc.enemy_physical_share >= %(physical_share_min)s
          WHEN 'healing'  THEN pc.enemy_healing_pm     >  %(healing_pm_p75)s
          WHEN 'cc'       THEN pc.enemy_cc_pm          >  %(cc_pm_p75)s
          WHEN 'pen'      THEN pc.opponent_champion_id IN (
                               SELECT o.champion_id FROM champion o
                               WHERE CASE %(pen_kind)s
                                       WHEN 'physical' THEN o.base_armor + o.armor_per_level * %(growth_levels)s
                                       ELSE o.base_magic_resist + o.magic_resist_per_level * %(growth_levels)s
                                     END >= %(pen_resist_min)s)
          ELSE FALSE
        END
) x
GROUP BY x.item_id
HAVING COUNT(*) >= %(min_games)s
"""

SQL_CLASS_EVIDENCE = """
SELECT
  COUNT(*) FILTER (WHERE pc.enemy_magic_share >= %(magic_share_min)s AND pc.completed_magic_resist) AS magic_games,
  COUNT(*) FILTER (WHERE pc.enemy_magic_share >= %(magic_share_min)s AND pc.completed_magic_resist AND pc.won) AS magic_wins,
  COUNT(*) FILTER (WHERE pc.enemy_physical_share >= %(physical_share_min)s AND pc.completed_armor) AS physical_games,
  COUNT(*) FILTER (WHERE pc.enemy_physical_share >= %(physical_share_min)s AND pc.completed_armor AND pc.won) AS physical_wins,
  COUNT(*) FILTER (WHERE pc.enemy_healing_pm > %(healing_pm_p75)s AND pc.completed_grievous_wounds) AS healing_games,
  COUNT(*) FILTER (WHERE pc.enemy_healing_pm > %(healing_pm_p75)s AND pc.completed_grievous_wounds AND pc.won) AS healing_wins,
  COUNT(*) FILTER (WHERE pc.enemy_cc_pm > %(cc_pm_p75)s AND pc.completed_tenacity) AS cc_games,
  COUNT(*) FILTER (WHERE pc.enemy_cc_pm > %(cc_pm_p75)s AND pc.completed_tenacity AND pc.won) AS cc_wins
FROM participant_core pc
WHERE pc.champion_id = %(champion_id)s AND pc.role = %(role)s
"""


@dataclass(frozen=True)
class EvidenceContext:
    champion: ChampionRef
    role: str
    opponent: ChampionRef
    profile: CompProfile
    pen_kind: str = MAGIC

    def params(self) -> dict:
        return {
            "champion_id": self.champion.champion_id, "role": self.role,
            "opponent_id": self.opponent.champion_id,
            "pen_kind": self.pen_kind, "pen_resist_min": PEN_RESIST_MIN,
            "growth_levels": REFERENCE_LEVEL - 1,
            "magic_share_min": MAGIC_SHARE_MIN, "physical_share_min": PHYSICAL_SHARE_MIN,
            "healing_pm_p75": self.profile.healing_pm_p75, "cc_pm_p75": self.profile.cc_pm_p75,
            "min_games": MIN_GAMES,
        }


def _evidence(games: int, wins: int, text: str) -> Evidence:
    low, high = wilson_interval(wins, games)
    return Evidence(games=games, wins=wins, win_rate=rate(wins, games),
                    ci_low=low, ci_high=high, condition_text=text)


def item_evidence(conn, ctx: EvidenceContext, rule_key: str,
                  item_ids: list[int]) -> dict[int, Evidence]:
    if not item_ids:
        return {}
    params = {**ctx.params(), "rule": rule_key, "item_ids": item_ids}
    text = condition_text(rule_key, ctx.profile, ctx.opponent, ctx.pen_kind)
    rows = conn.execute(SQL_ITEM_EVIDENCE, params).fetchall()
    return {r["item_id"]: _evidence(int(r["games"]), int(r["wins"]), text) for r in rows}


def class_evidence(conn, ctx: EvidenceContext, rule_keys: tuple[str, ...]) -> dict[str, Evidence]:
    wanted = [k for k in rule_keys if k in CLASS_RULES]
    if not wanted:
        return {}
    row = conn.execute(SQL_CLASS_EVIDENCE, ctx.params()).fetchone()
    out = {}
    for key in wanted:
        games, wins = int(row[f"{key}_games"]), int(row[f"{key}_wins"])
        if games >= MIN_GAMES:
            out[key] = _evidence(games, wins,
                                 condition_text(key, ctx.profile, ctx.opponent, ctx.pen_kind))
    return out
