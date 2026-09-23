"""Standalone template preview for LaneForge (port 8010).

Renders every template in docs/CONTRACT.md's table with realistic fake data
built from the frozen dataclasses in laneforge/queries/models.py, so the UI
can be judged without a database or the real blueprints.

    .venv/bin/python scripts/ui_preview.py        # http://127.0.0.1:8010/

Champion and item names and stats come from the checked-in Data Dragon files
(data/ddragon/*.json); matchup numbers are invented but internally consistent
(pick rate = games / sample size, intervals are real Wilson intervals).
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from flask import Flask, abort, flash, render_template, request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from laneforge.queries.models import (  # noqa: E402
    ROLE_LABELS, ROLES, BuildRow, ChampionOverview, ChampionRef, CompMember, CompProfile,
    DatasetSummary, Evidence, ItemRef, LadderAnswer, MatchDetail, MatchSummary, MatchupStat,
    ParticipantDetail, PurchaseRef, RoleStats, Rule, SavedBuild, SituationalAnswer, Suggestion,
    UserRef,
)

WEB_DIR = ROOT / "laneforge" / "web"
DDRAGON_DIR = ROOT / "data" / "ddragon"
DDRAGON_VERSION = "16.18.1"
CDN = "https://ddragon.leagueoflegends.com/cdn"
PORT = 8010
MIN_GAMES = 30
T0 = datetime(2026, 9, 18, 21, 14)

# --------------------------------------------------------------------------- helpers
# Local stand-ins for the Jinja globals/filters the real app registers (CONTRACT).


def champion_img(champion: ChampionRef) -> str:
    return f"{CDN}/{DDRAGON_VERSION}/img/champion/{champion.ddragon_key}.png"


def item_img(item: ItemRef) -> str:
    return f"{CDN}/{DDRAGON_VERSION}/img/item/{item.item_id}.png"


def f_pct(x: float | None) -> str:
    return "—" if x is None else f"{round(x * 100)}%"


def f_pct1(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def f_num(x: float | None) -> str:
    return "—" if x is None else f"{int(round(x)):,}"


def f_gold(x: float | None) -> str:
    return "—" if x is None else f"{int(x):,}"


def f_mmss(ms: int | None) -> str:
    if ms is None:
        return "—"
    total = int(ms) // 1000
    return f"{total // 60}:{total % 60:02d}"


def f_role_label(role: str | None) -> str:
    return ROLE_LABELS.get(role or "", role or "—")


FILTERS = {"pct": f_pct, "pct1": f_pct1, "num": f_num, "gold": f_gold,
           "mmss": f_mmss, "role_label": f_role_label}
GLOBALS = {"champion_img": champion_img, "item_img": item_img, "ddragon_version": DDRAGON_VERSION}


def wilson(wins: int, games: int, z: float = 1.96) -> tuple[float, float]:
    if games == 0:
        return (0.0, 0.0)
    p = wins / games
    denom = 1 + z * z / games
    centre = p + z * z / (2 * games)
    spread = z * math.sqrt(p * (1 - p) / games + z * z / (4 * games * games))
    return ((centre - spread) / denom, (centre + spread) / denom)


# --------------------------------------------------------------------------- catalogue

STAT_MAP = {"FlatMagicDamageMod": "ability_power", "FlatPhysicalDamageMod": "attack_damage",
            "FlatHPPoolMod": "health", "FlatMPPoolMod": "mana", "FlatArmorMod": "armor",
            "FlatSpellBlockMod": "magic_resist", "FlatMovementSpeedMod": "move_speed",
            "PercentAttackSpeedMod": "attack_speed_pct", "FlatCritChanceMod": "crit_chance_pct"}
DESC_MAP = {"Ability Haste": "ability_haste", "Lethality": "lethality", "Tenacity": "tenacity_pct",
            "Life Steal": "life_steal_pct", "Armor Penetration": "armor_pen_pct"}
GRIEVOUS = {"3165", "3075", "3033", "3123", "3916", "6609", "3076", "3011"}


def _desc_stats(description: str) -> dict:
    block = re.search(r"<stats>(.*?)</stats>", description or "")
    out: dict = {}
    if not block:
        return out
    for value, pct, label in re.findall(r"<attention>(\d+)(%?)</attention>\s*([A-Za-z ]+)", block.group(1)):
        key = DESC_MAP.get(label.strip())
        if label.strip() == "Magic Penetration":
            key = "magic_pen_pct" if pct else "magic_pen_flat"
        if key:
            out[key] = int(value) / 100 if (pct or key.endswith("_pct")) else int(value)
    return out


@lru_cache(maxsize=1)
def items_by_id() -> dict[int, ItemRef]:
    raw = json.loads((DDRAGON_DIR / "item.json").read_text())["data"]
    seen_names: set[str] = set()
    result: dict[int, ItemRef] = {}
    for key in sorted(raw, key=int):
        it = raw[key]
        if int(key) > 320000 or not it["gold"]["purchasable"] or not it.get("maps", {}).get("11"):
            continue
        if it["name"] in seen_names or it.get("requiredChampion") or it["gold"]["total"] == 0:
            continue
        seen_names.add(it["name"])
        tags = it.get("tags", [])
        is_boots = "Boots" in tags
        is_legendary = (not it.get("into")) and it["gold"]["total"] >= 2200 and not is_boots
        stats = {STAT_MAP[k]: v for k, v in it.get("stats", {}).items() if k in STAT_MAP}
        stats.update(_desc_stats(it.get("description", "")))
        result[int(key)] = ItemRef(item_id=int(key), name=it["name"], gold_cost=it["gold"]["total"],
                                   is_legendary=is_legendary, is_boots=is_boots,
                                   applies_grievous_wounds=key in GRIEVOUS, **stats)
    return result


def item(item_id: int) -> ItemRef:
    return items_by_id()[item_id]


@lru_cache(maxsize=1)
def champions_by_key() -> dict[str, ChampionRef]:
    raw = json.loads((DDRAGON_DIR / "champion.json").read_text())["data"]
    out = {}
    for key, c in raw.items():
        s = c["stats"]
        out[key] = ChampionRef(
            champion_id=int(c["key"]), name=c["name"], ddragon_key=key,
            base_health=int(s["hp"]), health_per_level=s["hpperlevel"],
            base_armor=int(s["armor"]), armor_per_level=s["armorperlevel"],
            base_magic_resist=int(s["spellblock"]), magic_resist_per_level=s["spellblockperlevel"],
            base_attack_damage=int(s["attackdamage"]), attack_damage_per_level=s["attackdamageperlevel"],
            base_attack_speed=s["attackspeed"], attack_speed_per_level_pct=s["attackspeedperlevel"] / 100)
    return out


def champ(key: str) -> ChampionRef:
    return champions_by_key()[key]


def all_champions() -> list[ChampionRef]:
    return sorted(champions_by_key().values(), key=lambda c: c.name)


def all_items() -> list[ItemRef]:
    return sorted(items_by_id().values(), key=lambda i: i.name)


def legendary_items() -> list[ItemRef]:
    return [i for i in all_items() if i.is_legendary and not i.is_boots]


# --------------------------------------------------------------------------- fixtures

LUDENS, SHADOWFLAME, RABADON, ZHONYA = 6655, 4645, 3089, 3157
MALIGNANCE, BANSHEE, MORELLO, MERCS, SORCS, STEELCAPS = 3118, 3102, 3165, 3111, 3020, 3047


def dataset(empty: bool = False) -> DatasetSummary:
    if empty:
        return DatasetSummary(patch=None)
    return DatasetSummary(patch="16.18", region="NA", matches=5000, participants=50000,
                          tiers=("GOLD", "PLATINUM"), first_start=T0 - timedelta(days=13),
                          last_start=T0)


def current_user() -> UserRef:
    return UserRef(user_id=4, display_name="midlane_marta", created_at=T0 - timedelta(days=2))


def build_row(item_ids: tuple[int, ...], games: int, wins: int, sample: int) -> BuildRow:
    lo, hi = wilson(wins, games)
    return BuildRow(items=tuple(item(i) for i in item_ids), games=games, wins=wins,
                    pick_rate=games / sample, win_rate=wins / games if games else 0.0,
                    ci_low=lo, ci_high=hi, sufficient=games >= MIN_GAMES)


def ladder_level1() -> LadderAnswer:
    n = 212
    rows = (build_row((LUDENS, SHADOWFLAME, RABADON), 58, 33, n),
            build_row((LUDENS, ZHONYA, RABADON), 41, 20, n),
            build_row((MALIGNANCE, SHADOWFLAME, RABADON), 34, 19, n),
            build_row((LUDENS, SHADOWFLAME, ZHONYA), 31, 15, n),
            build_row((LUDENS, BANSHEE, RABADON), 17, 10, n))
    return LadderAnswer(level=1, scope="matchup", granularity="sequence", sample_size=n,
                        label="vs Zed, mid: 212 games", fell_back=False, fallback_note=None, rows=rows)


def ladder_level3() -> LadderAnswer:
    n = 1840
    rows = (build_row((LUDENS, SHADOWFLAME, RABADON), 412, 213, n),
            build_row((MALIGNANCE, SHADOWFLAME, RABADON), 297, 158, n),
            build_row((LUDENS, ZHONYA, RABADON), 188, 94, n),
            build_row((MALIGNANCE, ZHONYA, RABADON), 121, 64, n),
            build_row((LUDENS, SHADOWFLAME, 3135), 26, 12, n))
    return LadderAnswer(level=3, scope="champion", granularity="sequence", sample_size=n,
                        label="all opponents: 1,840 games", fell_back=True,
                        fallback_note="not enough Ahri vs Zed games; showing Ahri, mid, all opponents",
                        rows=rows)


def ladder_level4() -> LadderAnswer:
    n = 94
    rows = tuple(build_row((i,), g, w, n) for i, g, w in
                 ((LUDENS, 71, 37), (RABADON, 60, 33), (SHADOWFLAME, 44, 21), (ZHONYA, 38, 20), (BANSHEE, 12, 5)))
    return LadderAnswer(level=4, scope="champion", granularity="item", sample_size=n,
                        label="all opponents: 94 games", fell_back=True,
                        fallback_note="not enough Ahri vs Zed games; showing Ahri, mid, all opponents, item by item",
                        rows=rows)


def evidence(games: int, wins: int, condition: str) -> Evidence:
    lo, hi = wilson(wins, games)
    return Evidence(games=games, wins=wins, win_rate=wins / games, ci_low=lo, ci_high=hi,
                    condition_text=condition)


def member(key: str, source: str, role: str | None, dmg: tuple[float, float, float],
           heal: float, cc: float) -> CompMember:
    return CompMember(champion=champ(key), role=role, source=source, physical_pm=dmg[0],
                      magic_pm=dmg[1], true_pm=dmg[2], healing_pm=heal, cc_pm=cc)


def situational_full() -> SituationalAnswer:
    """Zed + Lee Sin, Jinx, Thresh, Darius: physical-heavy with a lot of healing."""
    members = (member("Zed", "role", "MIDDLE", (612, 18, 71), 88, 1.1),
               member("LeeSin", "champion", None, (498, 102, 64), 214, 3.9),
               member("Jinx", "champion", None, (731, 41, 12), 96, 2.2),
               member("Thresh", "champion", None, (58, 214, 40), 31, 8.7),
               member("Darius", "champion", None, (520, 12, 186), 598, 2.4))
    profile = CompProfile(magic_share=0.13, physical_share=0.74, true_share=0.13, healing_pm=1027.0,
                          cc_pm=18.3, healing_pm_p75=842.0, cc_pm_p75=21.6, partial=False, members=members)
    physical = Rule(key="physical", reason="comp is 74% physical", item_class="armor")
    healing = Rule(key="healing", reason="comp heals 1,027 per minute, above the 75th percentile of 842",
                   item_class="anti-heal")
    cond_p = "comps with at least 55% physical damage"
    cond_h = "comps healing above the 75th percentile"
    suggestions = (
        Suggestion(item=item(STEELCAPS), rule=physical, score=1412.0,
                   score_text="+1,412 eHP per 1,000 gold", evidence=None),
        Suggestion(item=item(ZHONYA), rule=physical, score=611.0,
                   score_text="+611 eHP per 1,000 gold", evidence=evidence(236, 131, cond_p)),
        Suggestion(item=item(MORELLO), rule=healing, score=433.0,
                   score_text="+433 eHP per 1,000 gold, cuts healing 40%", evidence=evidence(97, 52, cond_h)),
    )
    return SituationalAnswer(profile=profile, triggered=(physical, healing), suggestions=suggestions,
                             class_evidence={"physical": evidence(388, 206, cond_p)})


def situational_partial() -> SituationalAnswer:
    """Zed + Lux, Galio only: a magic-heavy partial comp."""
    members = (member("Zed", "role", "MIDDLE", (612, 18, 71), 88, 1.1),
               member("Lux", "champion", None, (22, 842, 9), 12, 9.8),
               member("Galio", "champion", None, (61, 590, 14), 44, 11.2))
    profile = CompProfile(magic_share=0.61, physical_share=0.31, true_share=0.08, healing_pm=144.0,
                          cc_pm=22.1, healing_pm_p75=842.0, cc_pm_p75=21.6, partial=True, members=members)
    magic = Rule(key="magic", reason="comp is 61% magic", item_class="magic resist")
    cc = Rule(key="cc", reason="comp applies 22.1 s of crowd control per minute, above the 75th percentile of 21.6",
              item_class="tenacity")
    cond = "comps with at least 55% magic damage"
    suggestions = (
        Suggestion(item=item(BANSHEE), rule=magic, score=842.0, score_text="+842 eHP per 1,000 gold",
                   evidence=evidence(164, 89, cond)),
        Suggestion(item=item(MERCS), rule=magic, score=790.0, score_text="+790 eHP per 1,000 gold",
                   evidence=None),
        Suggestion(item=item(MERCS), rule=cc, score=30.0, score_text="30% tenacity for 1,250 gold",
                   evidence=evidence(58, 31, "comps with crowd control above the 75th percentile")),
    )
    return SituationalAnswer(profile=profile, triggered=(magic, cc), suggestions=suggestions,
                             class_evidence={"magic": evidence(301, 160, cond)})


def enemies_full() -> tuple[ChampionRef, ...]:
    return tuple(champ(k) for k in ("LeeSin", "Jinx", "Thresh", "Darius"))


def saved_builds() -> list[SavedBuild]:
    user = current_user()
    totals = lambda ids: {k: sum(getattr(item(i), k) for i in ids)  # noqa: E731
                          for k in ("ability_power", "health", "mana", "armor", "magic_resist",
                                    "ability_haste", "magic_pen_flat", "magic_pen_pct", "move_speed")}
    observed = (LUDENS, SHADOWFLAME, RABADON)
    custom = (LUDENS, ZHONYA, BANSHEE)
    return [
        SavedBuild(build_id=9, user=user, name="Zhonya’s second into dive", notes=(
            "Swapped Rabadon’s for Banshee’s after losing twice to Zed plus Lee Sin ganks. "
            "Hold Zhonya’s for Death Mark, not for the Lee kick."),
            champion=champ("Ahri"), role="MIDDLE", opponent=champ("Zed"), enemies=enemies_full(),
            items=tuple(item(i) for i in custom), is_customized=True,
            created_at=T0 - timedelta(hours=5), stat_totals=totals(custom)),
        SavedBuild(build_id=7, user=user, name="Ahri vs Zed, Luden’s first", notes=None,
                   champion=champ("Ahri"), role="MIDDLE", opponent=champ("Zed"), enemies=enemies_full(),
                   items=tuple(item(i) for i in observed), is_customized=False,
                   created_at=T0 - timedelta(days=1, hours=3), stat_totals=totals(observed)),
    ]


# Ten players, blue team 100 won. (champion, role, measures, purchases as (minute, second, item))
MATCH_PLAYERS = (
    (100, "Darius", "TOP", (19840, 1120, 6210, 14320, 38), ((0, 5, 1054), (0, 5, 2003), (7, 12, 3047), (15, 40, 3071), (24, 2, 3053), (31, 50, 3075))),
    (100, "LeeSin", "JUNGLE", (14210, 3920, 2870, 8810, 64), ((0, 4, 1036), (6, 51, 3134), (13, 20, 3142), (21, 5, 3047), (29, 44, 6610))),
    (100, "Ahri", "MIDDLE", (1290, 24810, 1650, 3120, 41), ((0, 6, 1056), (0, 6, 2003), (6, 33, 1082), (8, 2, 3802), (13, 58, 6655), (15, 9, 3020), (21, 30, 4645), (29, 17, 3089))),
    (100, "Jinx", "BOTTOM", (27110, 820, 1440, 2210, 22), ((0, 3, 1055), (9, 40, 1038), (14, 22, 6672), (16, 1, 3006), (23, 48, 3031), (32, 12, 3036))),
    (100, "Thresh", "UTILITY", (2010, 7420, 1120, 1320, 118), ((0, 2, 3858), (0, 2, 2003), (11, 5, 2055), (14, 55, 3190), (26, 30, 3109))),
    (200, "Aatrox", "TOP", (21420, 610, 1920, 17430, 27), ((0, 5, 1055), (8, 14, 1036), (12, 51, 3047), (17, 2, 6610), (28, 33, 6333))),
    (200, "Vi", "JUNGLE", (13880, 910, 2310, 6120, 71), ((0, 4, 1036), (9, 12, 3067), (15, 30, 3071), (19, 58, 3047), (30, 5, 3748))),
    (200, "Zed", "MIDDLE", (22940, 540, 3120, 1840, 9), ((0, 6, 1036), (0, 6, 2003), (7, 45, 3134), (12, 38, 3142), (20, 41, 6610), (30, 2, 6333))),
    (200, "Kaisa", "BOTTOM", (18620, 7210, 1010, 2980, 18), ((0, 3, 1055), (10, 20, 1038), (15, 48, 6672), (17, 30, 3006), (27, 10, 3031))),
    (200, "Nautilus", "UTILITY", (1210, 5610, 2240, 890, 142), ((0, 2, 3858), (10, 40, 2055), (16, 2, 3190), (23, 18, 3047))),
)


def match_detail() -> MatchDetail:
    participants = []
    for n, (team, key, role, measures, buys) in enumerate(MATCH_PLAYERS, start=1):
        purchases = tuple(PurchaseRef(event_number=i + 1, game_time_ms=(m * 60 + s) * 1000, item=item(it))
                          for i, (m, s, it) in enumerate(buys) if it in items_by_id())
        phys, magic, true, heal, cc = measures
        participants.append(ParticipantDetail(participant_number=n, team=team, role=role, champion=champ(key),
                                              won=team == 100, physical_damage=phys, magic_damage=magic,
                                              true_damage=true, healing_done=heal, cc_seconds=cc,
                                              purchases=purchases))
    return MatchDetail(match_id="NA1_5361184420", game_version="16.18.712.4450", start_time=T0,
                       duration_seconds=33 * 60 + 41, winning_team=100, seed_tier="PLATINUM",
                       participants=tuple(participants))


def recent_matches() -> list[MatchSummary]:
    rosters = [
        (("Darius", "LeeSin", "Ahri", "Jinx", "Thresh"), ("Aatrox", "Vi", "Zed", "Kaisa", "Nautilus"), 100),
        (("Garen", "Vi", "Syndra", "Kaisa", "Lux"), ("Darius", "LeeSin", "Yasuo", "Jinx", "Thresh"), 200),
        (("Aatrox", "Ekko", "Orianna", "Jinx", "Nautilus"), ("Garen", "LeeSin", "Ahri", "Kaisa", "Thresh"), 200),
        (("Sylas", "Vi", "Viktor", "Kaisa", "Thresh"), ("Darius", "Ekko", "Katarina", "Jinx", "Lux"), 100),
        (("Galio", "LeeSin", "Akali", "Jinx", "Nautilus"), ("Aatrox", "Vi", "Leblanc", "Kaisa", "Thresh"), 100),
    ]
    out = []
    for i, (blue, red, winner) in enumerate(rosters):
        out.append(MatchSummary(match_id=f"NA1_53611844{20 - i * 3:02d}", game_version="16.18.712.4450",
                                start_time=T0 - timedelta(minutes=47 * i), duration_seconds=1500 + 131 * i,
                                winning_team=winner, seed_tier="PLATINUM" if i % 2 == 0 else "GOLD",
                                blue=tuple(champ(k) for k in blue), red=tuple(champ(k) for k in red)))
    return out


def champion_overview() -> ChampionOverview:
    ahri = champ("Ahri")
    by_role = (
        RoleStats(role="MIDDLE", games=2318, wins=1203, win_rate=1203 / 2318, physical_pm=41.0, magic_pm=716.0,
                  true_pm=58.0, healing_pm=92.0, cc_pm=4.1, profile_source="role"),
        RoleStats(role="UTILITY", games=24, wins=11, win_rate=11 / 24, physical_pm=22.0, magic_pm=455.0,
                  true_pm=31.0, healing_pm=61.0, cc_pm=3.8, profile_source="champion"),
    )
    opponents = (("Zed", 212, 109), ("Syndra", 188, 91), ("Yasuo", 171, 94), ("Orianna", 149, 76),
                 ("Viktor", 133, 70), ("Akali", 97, 45), ("Katarina", 62, 34), ("Leblanc", 21, 8))
    matchups = tuple(MatchupStat(opponent=champ(k), role="MIDDLE", games=g, wins=w, win_rate=w / g)
                     for k, g, w in opponents)
    return ChampionOverview(champion=ahri, games=2342, wins=1214, win_rate=1214 / 2342, by_role=by_role,
                            top_matchups=matchups)


def item_usage() -> list:
    from types import SimpleNamespace as NS
    rows = (("Ahri", "MIDDLE", 1622, 842), ("Syndra", "MIDDLE", 1310, 671), ("Orianna", "MIDDLE", 988, 489),
            ("Lux", "UTILITY", 402, 214), ("Viktor", "MIDDLE", 377, 185), ("Vex", "MIDDLE", 19, 9))
    return [NS(champion=champ(k), role=r, games=g, wins=w, win_rate=w / g) for k, r, g, w in rows]


# --------------------------------------------------------------------------- pages

MATCHUP_QS = "champion=103&role=MIDDLE&opponent=238&enemy=64&enemy=222&enemy=412&enemy=122"
PARTIAL_QS = "champion=103&role=MIDDLE&opponent=238&enemy=99&enemy=3"


def _matchup_ctx(ladder, situational, enemies, qs):
    return {"champion": champ("Ahri"), "role": "MIDDLE", "opponent": champ("Zed"), "enemies": enemies,
            "ladder": ladder, "situational": situational, "query_string": qs}


PAGES = {
    # name: (template, context factory, description, query string for request.args)
    "index": ("index.html", lambda: {"champions": all_champions(), "roles": list(ROLES), "dataset": dataset()},
              "Matchup form", "champion=103&role=MIDDLE&opponent=238"),
    "index-empty": ("index.html", lambda: {"champions": all_champions(), "roles": list(ROLES),
                                           "dataset": dataset(empty=True)}, "Matchup form, no matches loaded", ""),
    "matchup": ("matchup.html", lambda: _matchup_ctx(ladder_level1(), situational_full(), enemies_full(), MATCHUP_QS),
                "Dossier, level 1, rendered server-side (no-JS path)", MATCHUP_QS),
    "matchup-htmx": ("matchup.html", lambda: _matchup_ctx(None, None, enemies_full(), MATCHUP_QS),
                     "Dossier shell; blocks load over htmx from /matchup/core and /matchup/situational", MATCHUP_QS),
    "fallback": ("matchup.html", lambda: _matchup_ctx(ladder_level3(), situational_partial(),
                                                      (champ("Lux"), champ("Galio")), PARTIAL_QS),
                 "Dossier, fell back to level 3, partial comp", PARTIAL_QS),
    "fallback-item": ("matchup.html", lambda: _matchup_ctx(ladder_level4(), situational_partial(),
                                                           (champ("Lux"), champ("Galio")), PARTIAL_QS),
                      "Dossier, level 4 per-item rows", PARTIAL_QS),
    "core": ("partials/core.html", lambda: {"ladder": ladder_level1(), "champion": champ("Ahri"), "role": "MIDDLE",
                                            "opponent": champ("Zed")}, "Partial: core builds", MATCHUP_QS),
    "situational": ("partials/situational.html", lambda: {"situational": situational_full(), "champion": champ("Ahri"),
                                                          "role": "MIDDLE", "opponent": champ("Zed"),
                                                          "enemies": enemies_full()},
                    "Partial: situational", MATCHUP_QS),
    "champions": ("champions.html", lambda: {"champions": all_champions()}, "Champion catalogue", ""),
    "champion": ("champion.html", lambda: {"champion": champ("Ahri"), "overview": champion_overview()},
                 "Champion overview", ""),
    "items": ("items.html", lambda: {"items": all_items()}, "Item catalogue", ""),
    "item": ("item.html", lambda: {"item": item(SHADOWFLAME), "usage": item_usage()}, "Item page", ""),
    "matches": ("matches.html", lambda: {"matches": recent_matches()}, "Recent matches", ""),
    "match": ("match.html", lambda: {"match": match_detail()}, "Match team sheet", ""),
    "signin": ("signin.html", lambda: {}, "Sign in", "next=/builds"),
    "builds": ("builds.html", lambda: {"builds": saved_builds()}, "Saved builds", ""),
    "builds-empty": ("builds.html", lambda: {"builds": []}, "Saved builds, none yet", ""),
    "build": ("build.html", lambda: {"build": saved_builds()[0], "legendary_items": legendary_items()},
              "Saved build page (with a flash message)", ""),
    "base": ("base.html", lambda: {}, "Bare layout", ""),
    "400": ("errors/400.html", lambda: {"message": "Pick two different champions: Ahri cannot be her own lane opponent."},
            "400 page", ""),
    "404": ("errors/404.html", lambda: {"message": "There is no saved build No. 412. It may have been deleted."},
            "404 page", ""),
}


def create_app() -> Flask:
    app = Flask(__name__, template_folder=str(WEB_DIR / "templates"), static_folder=str(WEB_DIR / "static"))
    app.secret_key = "preview-only"
    app.jinja_env.globals.update(GLOBALS)
    app.jinja_env.filters.update(FILTERS)

    @app.context_processor
    def base_context():
        anon = request.args.get("anon") == "1"
        return {"current_user": None if anon else current_user(), "dataset": dataset()}

    @app.get("/")
    def preview_index():
        return render_template_string_index()

    @app.get("/preview/<name>")
    def preview(name: str):
        if name not in PAGES:
            abort(404)
        template, factory, _, _ = PAGES[name]
        if name == "build":
            flash("Items saved. The build is now marked customized.")
        status = int(name) if name.isdigit() else 200
        return render_template(template, **factory()), status

    @app.get("/matchup/core")
    def partial_core():
        ladder = ladder_level1() if request.args.get("enemy") != "99" else ladder_level3()
        return render_template("partials/core.html", ladder=ladder, champion=champ("Ahri"), role="MIDDLE",
                               opponent=champ("Zed"))

    @app.get("/matchup/situational")
    def partial_situational():
        return render_template("partials/situational.html", situational=situational_full(),
                               champion=champ("Ahri"), role="MIDDLE", opponent=champ("Zed"),
                               enemies=enemies_full())

    @app.errorhandler(404)
    def not_found(_err):
        return render_template("errors/404.html", message=None), 404

    return app


def render_template_string_index() -> str:
    from flask import render_template_string
    rows = "".join(
        f'<li><a href="/preview/{name}{"?" + qs if qs else ""}">{name}</a> '
        f'<span class="mute">{template} · {desc}</span></li>'
        for name, (template, _, desc, qs) in PAGES.items())
    body = ('{% extends "base.html" %}{% block content %}<h1 class="headline">Template preview</h1>'
            '<p class="lede-small">Fake data from the query dataclasses. Add <code>?anon=1</code> to any page to '
            'see it signed out.</p><ol class="entries">' + rows + '</ol>{% endblock %}')
    return render_template_string(body)


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=PORT, debug=True)
