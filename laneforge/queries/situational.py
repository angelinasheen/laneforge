"""Draft 4 "Situational items": comp profile, rules, stat-model scores, evidence."""
from __future__ import annotations

from laneforge.queries import evidence as ev
from laneforge.queries._rows import item_from_row, load_champions, ITEM_COLUMNS
from laneforge.queries.errors import ValidationError
from laneforge.queries.models import (
    ChampionRef, CompMember, CompProfile, Evidence, ItemRef, Rule, SituationalAnswer, Suggestion,
)
from laneforge.queries.rules import RULE_ORDER, item_matches, pen_rule, triggered_rules
from laneforge.queries.statmodel import (
    MAGIC, PHYSICAL, defensive_score, penetration_score,
)
from laneforge.queries.validate import require_distinct_matchup, require_enemies, require_role

MAX_SUGGESTIONS_PER_RULE = 5
PROFILE_FIELDS = ("physical_pm", "magic_pm", "true_pm", "healing_pm", "cc_pm")

SQL_OPPONENT_PROFILE = """
SELECT e.source, e.champion_games, e.physical_pm, e.magic_pm, e.true_pm, e.healing_pm, e.cc_pm
FROM champion_effective_profile e
WHERE e.champion_id = %(champion_id)s AND e.role = %(role)s
"""

SQL_ENEMY_PROFILES = """
SELECT cp.champion_id, cp.games, cp.physical_pm, cp.magic_pm, cp.true_pm, cp.healing_pm, cp.cc_pm
FROM champion_profile cp
WHERE cp.champion_id = ANY(%(ids)s)
"""

SQL_THRESHOLDS = "SELECT healing_pm_p75, cc_pm_p75 FROM comp_thresholds"

# Legendaries and boots that belong to at least one situational class.
SQL_SITUATIONAL_CANDIDATES = f"""
SELECT {ITEM_COLUMNS}
FROM item
WHERE (is_legendary OR is_boots)
  AND (magic_resist > 0 OR armor > 0 OR applies_grievous_wounds OR tenacity_pct > 0
       OR lethality > 0 OR armor_pen_pct > 0 OR magic_pen_flat > 0 OR magic_pen_pct > 0)
ORDER BY item_id
"""

# Damage type of the champion's most common first legendary at this role.
SQL_FIRST_ITEM_KIND = """
SELECT i.attack_damage, i.lethality, COUNT(*) AS games
FROM participant_core pc
JOIN item i ON i.item_id = pc.item1
WHERE pc.champion_id = %(champion_id)s AND pc.role = %(role)s
GROUP BY i.item_id, i.attack_damage, i.lethality
ORDER BY games DESC, i.item_id
LIMIT 1
"""


def _member(champion: ChampionRef, role: str | None, source: str, row: dict | None) -> CompMember:
    values = {k: float(row[k]) if row and row[k] is not None else 0.0 for k in PROFILE_FIELDS}
    return CompMember(champion=champion, role=role, source=source, **values)


def _members(conn, role, opponent: ChampionRef, enemies: tuple[ChampionRef, ...]):
    opp_row = conn.execute(SQL_OPPONENT_PROFILE,
                           {"champion_id": opponent.champion_id, "role": role}).fetchone()
    opp_source = opp_row["source"] if opp_row and opp_row["champion_games"] > 0 else "none"
    rows = conn.execute(SQL_ENEMY_PROFILES,
                        {"ids": [c.champion_id for c in enemies]}).fetchall() if enemies else []
    by_id = {r["champion_id"]: r for r in rows}
    others = tuple(
        _member(c, None, "champion" if c.champion_id in by_id else "none", by_id.get(c.champion_id))
        for c in enemies
    )
    return (_member(opponent, role, opp_source, opp_row if opp_source != "none" else None),) + others


def _share(part: float, total: float) -> float:
    return part / total if total > 0 else 0.0


def _profile_from(members: tuple[CompMember, ...], thresholds: dict | None,
                  n_enemies: int) -> CompProfile:
    physical = sum(m.physical_pm for m in members)
    magic = sum(m.magic_pm for m in members)
    true = sum(m.true_pm for m in members)
    total = physical + magic + true
    p75 = thresholds or {}
    return CompProfile(
        magic_share=_share(magic, total), physical_share=_share(physical, total),
        true_share=_share(true, total),
        healing_pm=sum(m.healing_pm for m in members), cc_pm=sum(m.cc_pm for m in members),
        healing_pm_p75=_opt_float(p75.get("healing_pm_p75")),
        cc_pm_p75=_opt_float(p75.get("cc_pm_p75")),
        partial=n_enemies < 4, members=members,
    )


def _opt_float(value) -> float | None:
    return float(value) if value is not None else None


def _resolve(conn, champion_ids: list[int]) -> dict[int, ChampionRef]:
    refs = load_champions(conn, champion_ids)
    missing = [i for i in champion_ids if i not in refs]
    if missing:
        raise ValidationError(f"Unknown champion id {missing[0]}.")
    return refs


def comp_profile(conn, role: str, opponent_champion_id: int,
                 enemy_champion_ids) -> CompProfile:
    require_role(role)
    enemy_ids = tuple(enemy_champion_ids)
    refs = _resolve(conn, [opponent_champion_id, *enemy_ids])
    members = _members(conn, role, refs[opponent_champion_id], tuple(refs[i] for i in enemy_ids))
    thresholds = conn.execute(SQL_THRESHOLDS).fetchone()
    return _profile_from(members, thresholds, len(enemy_ids))


SQL_DAMAGE_KIND = """
SELECT physical_pm, magic_pm
FROM champion_effective_profile
WHERE champion_id = %(champion_id)s AND role = %(role)s
"""


def pen_kind(conn, champion_id: int, role: str) -> str:
    """Which resist the champion's damage runs into: their own threat profile
    decides (physical vs magic per minute at this role, with the 50-game
    fallback built into the view). With no games at all, the most common first
    legendary decides; with no data of any kind, magic."""
    row = conn.execute(SQL_DAMAGE_KIND, {"champion_id": champion_id, "role": role}).fetchone()
    if row and row["physical_pm"] is not None and row["magic_pm"] is not None \
            and float(row["physical_pm"]) != float(row["magic_pm"]):
        return PHYSICAL if float(row["physical_pm"]) > float(row["magic_pm"]) else MAGIC
    row = conn.execute(SQL_FIRST_ITEM_KIND, {"champion_id": champion_id, "role": role}).fetchone()
    if row and (row["attack_damage"] > 0 or row["lethality"] > 0):
        return PHYSICAL
    return MAGIC


def _score(rule: Rule, champion: ChampionRef, item: ItemRef, profile: CompProfile,
           opponent: ChampionRef, kind: str) -> tuple[float, str]:
    if rule.key == "pen":
        score = penetration_score(item, opponent, kind=kind)
        return score, f"+{score:.0%} damage to {opponent.name}"
    score = defensive_score(champion, item, profile)
    if score > 0:
        return score, f"+{score:,.0f} effective HP per 1,000 gold"
    return score, "no flat defensive stats; its value is in the passive"


def _suggestions_for(rule, candidates, champion, profile, opponent, kind, evidence_by_item):
    scored = []
    for item in candidates:
        if not item_matches(rule.key, item, kind):
            continue
        score, text = _score(rule, champion, item, profile, opponent, kind)
        scored.append(Suggestion(item=item, rule=rule, score=score, score_text=text,
                                 evidence=evidence_by_item.get(item.item_id)))
    scored.sort(key=lambda s: (-s.score, s.item.name))
    return tuple(scored[:MAX_SUGGESTIONS_PER_RULE])


def _rules(profile: CompProfile, opponent: ChampionRef, kind: str) -> tuple[Rule, ...]:
    extra = pen_rule(opponent, kind)
    rules = triggered_rules(profile) + ((extra,) if extra else ())
    return tuple(sorted(rules, key=lambda r: RULE_ORDER.index(r.key)))


def situational_items(conn, champion_id: int, role: str, opponent_champion_id: int,
                      enemy_champion_ids) -> SituationalAnswer:
    require_role(role)
    require_distinct_matchup(champion_id, opponent_champion_id)
    enemy_ids = require_enemies(champion_id, opponent_champion_id, enemy_champion_ids)
    champion = _resolve(conn, [champion_id])[champion_id]
    profile = comp_profile(conn, role, opponent_champion_id, enemy_ids)
    opponent = profile.members[0].champion
    kind = pen_kind(conn, champion_id, role)
    rules = _rules(profile, opponent, kind)
    if not rules:
        return SituationalAnswer(profile=profile, triggered=(), suggestions=(), class_evidence={})
    candidates = [item_from_row(r) for r in conn.execute(SQL_SITUATIONAL_CANDIDATES).fetchall()]
    context = ev.EvidenceContext(champion=champion, role=role, opponent=opponent,
                                 profile=profile, pen_kind=kind)
    suggestions: list[Suggestion] = []
    for rule in rules:
        matching = [i.item_id for i in candidates if item_matches(rule.key, i, kind)]
        by_item = ev.item_evidence(conn, context, rule.key, matching)
        suggestions.extend(_suggestions_for(rule, candidates, champion, profile, opponent,
                                            kind, by_item))
    class_evidence: dict[str, Evidence] = ev.class_evidence(conn, context,
                                                           tuple(r.key for r in rules))
    return SituationalAnswer(profile=profile, triggered=rules, suggestions=tuple(suggestions),
                             class_evidence=class_evidence)
