"""Draft 4 "Stat model": flat-stat arithmetic, pure functions, no database.

Effective HP against a comp weights armor by the comp's physical share, magic
resist by its magic share, and treats the remaining (true) share as
unmitigated. Penetration is scored as the change in the damage multiplier
against the lane opponent's resist at the reference level.
"""
from __future__ import annotations

from laneforge.queries.models import REFERENCE_LEVEL, ChampionRef, CompProfile, ItemRef

GOLD_UNIT = 1000.0
PHYSICAL = "physical"
MAGIC = "magic"


def resist_at_level(base: float, per_level: float, level: int = REFERENCE_LEVEL) -> float:
    """Stat at `level` = base + growth * (level - 1)."""
    return float(base) + float(per_level) * (level - 1)


def damage_multiplier(resist: float) -> float:
    """Fraction of pre-mitigation damage taken: 100 / (100 + resist)."""
    return 100.0 / (100.0 + max(0.0, resist))


def effective_hp(hp: float, armor: float, magic_resist: float,
                 physical_share: float, magic_share: float) -> float:
    """HP needed to be chewed through by this comp's damage mix."""
    true_share = max(0.0, 1.0 - physical_share - magic_share)
    return (hp * (1 + armor / 100.0) * physical_share
            + hp * (1 + magic_resist / 100.0) * magic_share
            + hp * true_share)


def champion_hp_armor_mr(champion: ChampionRef, level: int = REFERENCE_LEVEL) -> tuple[float, float, float]:
    return (resist_at_level(champion.base_health, champion.health_per_level, level),
            resist_at_level(champion.base_armor, champion.armor_per_level, level),
            resist_at_level(champion.base_magic_resist, champion.magic_resist_per_level, level))


def defensive_gain(champion: ChampionRef, item: ItemRef, profile: CompProfile,
                   level: int = REFERENCE_LEVEL) -> float:
    """Effective HP the item adds (absolute, not per gold)."""
    hp, armor, mr = champion_hp_armor_mr(champion, level)
    before = effective_hp(hp, armor, mr, profile.physical_share, profile.magic_share)
    after = effective_hp(hp + item.health, armor + item.armor, mr + item.magic_resist,
                         profile.physical_share, profile.magic_share)
    return after - before


def defensive_score(champion: ChampionRef, item: ItemRef, profile: CompProfile,
                    level: int = REFERENCE_LEVEL) -> float:
    """Effective HP gained per 1,000 gold (0 for a free item)."""
    if item.gold_cost <= 0:
        return 0.0
    return defensive_gain(champion, item, profile, level) / item.gold_cost * GOLD_UNIT


def opponent_resist(opponent: ChampionRef, kind: str, level: int = REFERENCE_LEVEL) -> float:
    if kind == PHYSICAL:
        return resist_at_level(opponent.base_armor, opponent.armor_per_level, level)
    return resist_at_level(opponent.base_magic_resist, opponent.magic_resist_per_level, level)


def resist_after_pen(resist: float, pen_pct: float, flat: float) -> float:
    """max(0, resist * (1 - pen%) - flat)."""
    return max(0.0, resist * (1 - pen_pct) - flat)


def _pen_for(item: ItemRef, kind: str) -> tuple[float, float]:
    if kind == PHYSICAL:
        return item.armor_pen_pct, item.lethality
    return item.magic_pen_pct, item.magic_pen_flat


def penetration_score(item: ItemRef, opponent: ChampionRef, level: int = REFERENCE_LEVEL,
                      kind: str | None = None) -> float:
    """multiplier_after / multiplier_before - 1 against the opponent's resist.

    With `kind=None` the item's better side (armor or magic) is scored."""
    kinds = (kind,) if kind else (PHYSICAL, MAGIC)
    best = 0.0
    for k in kinds:
        resist = opponent_resist(opponent, k, level)
        pen_pct, flat = _pen_for(item, k)
        after = resist_after_pen(resist, pen_pct, flat)
        best = max(best, damage_multiplier(after) / damage_multiplier(resist) - 1)
    return best
