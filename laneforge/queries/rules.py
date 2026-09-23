"""Draft 4 situational rules: comp feature -> item class. Pure, no DB."""
from __future__ import annotations

from laneforge.queries.models import CompProfile, ChampionRef, ItemRef, Rule
from laneforge.queries.statmodel import MAGIC, PHYSICAL, opponent_resist

MAGIC_SHARE_MIN = 0.55
PHYSICAL_SHARE_MIN = 0.55
PEN_RESIST_MIN = 60.0          # opponent resist at the reference level that triggers 'pen'
RULE_ORDER = ("magic", "physical", "healing", "cc", "pen")

ITEM_CLASSES = {
    "magic": "magic resist",
    "physical": "armor",
    "healing": "anti-heal",
    "cc": "tenacity",
    "pen": "penetration",
}


def triggered_rules(profile: CompProfile) -> tuple[Rule, ...]:
    """The four comp rules, in RULE_ORDER. Healing and CC compare strictly above p75."""
    rules = []
    if profile.magic_share >= MAGIC_SHARE_MIN:
        rules.append(Rule("magic", f"The comp is {profile.magic_share:.0%} magic damage",
                          ITEM_CLASSES["magic"]))
    if profile.physical_share >= PHYSICAL_SHARE_MIN:
        rules.append(Rule("physical", f"The comp is {profile.physical_share:.0%} physical damage",
                          ITEM_CLASSES["physical"]))
    if profile.healing_pm_p75 is not None and profile.healing_pm > profile.healing_pm_p75:
        rules.append(Rule("healing",
                          f"The comp heals {profile.healing_pm:,.0f} per minute, above the "
                          f"75th percentile of comps ({profile.healing_pm_p75:,.0f})",
                          ITEM_CLASSES["healing"]))
    if profile.cc_pm_p75 is not None and profile.cc_pm > profile.cc_pm_p75:
        rules.append(Rule("cc",
                          f"The comp deals {profile.cc_pm:.1f} s of crowd control per minute, above "
                          f"the 75th percentile of comps ({profile.cc_pm_p75:.1f} s)",
                          ITEM_CLASSES["cc"]))
    return tuple(rules)


def pen_rule(opponent: ChampionRef, kind: str) -> Rule | None:
    """Always-on fifth rule: the lane opponent's relevant resist at level 11 >= 60."""
    resist = opponent_resist(opponent, kind)
    if resist < PEN_RESIST_MIN:
        return None
    stat = "armor" if kind == PHYSICAL else "magic resist"
    item_class = "armor penetration" if kind == PHYSICAL else "magic penetration"
    return Rule("pen", f"{opponent.name} has {resist:.0f} {stat} at level 11", item_class)


def item_matches(rule_key: str, item: ItemRef, pen_kind: str = MAGIC) -> bool:
    if rule_key == "magic":
        return item.magic_resist > 0
    if rule_key == "physical":
        return item.armor > 0
    if rule_key == "healing":
        return item.applies_grievous_wounds
    if rule_key == "cc":
        return item.tenacity_pct > 0
    if rule_key == "pen" and pen_kind == PHYSICAL:
        return item.lethality > 0 or item.armor_pen_pct > 0
    if rule_key == "pen":
        return item.magic_pen_flat > 0 or item.magic_pen_pct > 0
    return False


def condition_text(rule_key: str, profile: CompProfile, opponent: ChampionRef,
                   pen_kind: str = MAGIC) -> str:
    if rule_key == "magic":
        return f"comps with at least {MAGIC_SHARE_MIN:.0%} magic damage"
    if rule_key == "physical":
        return f"comps with at least {PHYSICAL_SHARE_MIN:.0%} physical damage"
    if rule_key == "healing":
        return f"comps healing more than {profile.healing_pm_p75 or 0:,.0f} per minute"
    if rule_key == "cc":
        return f"comps with more than {profile.cc_pm_p75 or 0:.1f} s of crowd control per minute"
    stat = "armor" if pen_kind == PHYSICAL else "magic resist"
    return f"lane opponents with at least {PEN_RESIST_MIN:.0f} {stat} at level 11"
