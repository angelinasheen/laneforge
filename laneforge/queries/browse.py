"""Browsing pages: matches, one match, one champion's overview."""
from __future__ import annotations

from laneforge.queries._rows import load_champions, load_items, rate
from laneforge.queries.errors import ValidationError
from laneforge.queries.models import (
    ROLES, ChampionOverview, MatchDetail, MatchSummary, MatchupStat,
    ParticipantDetail, PurchaseRef, RoleStats,
)

MAX_MATCHES = 200
TOP_MATCHUPS = 8

SQL_RECENT_MATCHES = """
SELECT m.match_id, m.game_version, m.start_time, m.duration_seconds, m.winning_team, m.seed_tier,
       array_agg(p.champion_id ORDER BY array_position(ARRAY['TOP','JUNGLE','MIDDLE','BOTTOM','UTILITY']::varchar[], p.role))
         FILTER (WHERE p.team = 100) AS blue_ids,
       array_agg(p.champion_id ORDER BY array_position(ARRAY['TOP','JUNGLE','MIDDLE','BOTTOM','UTILITY']::varchar[], p.role))
         FILTER (WHERE p.team = 200) AS red_ids
FROM (SELECT * FROM match
      WHERE %(tier)s::text IS NULL OR seed_tier = %(tier)s::text
      ORDER BY start_time DESC, match_id
      LIMIT %(limit)s) m
JOIN participant p ON p.match_id = m.match_id
GROUP BY m.match_id, m.game_version, m.start_time, m.duration_seconds, m.winning_team, m.seed_tier
ORDER BY m.start_time DESC, m.match_id
"""

SQL_MATCH = """
SELECT match_id, game_version, start_time, duration_seconds, winning_team, seed_tier
FROM match WHERE match_id = %(match_id)s
"""
SQL_MATCH_PARTICIPANTS = """
SELECT p.*, (p.team = m.winning_team) AS won
FROM participant p JOIN match m ON m.match_id = p.match_id
WHERE p.match_id = %(match_id)s
ORDER BY p.participant_number
"""
SQL_MATCH_PURCHASES = """
SELECT participant_number, event_number, game_time_ms, item_id
FROM purchase_event
WHERE match_id = %(match_id)s
ORDER BY participant_number, game_time_ms, event_number
"""

SQL_CHAMPION_ROLE_RESULTS = """
SELECT pc.role, COUNT(*) AS games, SUM(CASE WHEN pc.won THEN 1 ELSE 0 END) AS wins
FROM participant_core pc
WHERE pc.champion_id = %(champion_id)s
GROUP BY pc.role
"""
SQL_CHAMPION_PROFILES = """
SELECT role, source, champion_games, physical_pm, magic_pm, true_pm, healing_pm, cc_pm
FROM champion_effective_profile
WHERE champion_id = %(champion_id)s
"""
SQL_TOP_MATCHUPS = """
SELECT pc.opponent_champion_id, pc.role, COUNT(*) AS games,
       SUM(CASE WHEN pc.won THEN 1 ELSE 0 END) AS wins
FROM participant_core pc
WHERE pc.champion_id = %(champion_id)s
GROUP BY pc.opponent_champion_id, pc.role
ORDER BY games DESC, pc.role, pc.opponent_champion_id
LIMIT %(limit)s
"""


def recent_matches(conn, limit: int = 50, tier: str | None = None) -> list[MatchSummary]:
    limit = max(1, min(int(limit), MAX_MATCHES))
    rows = conn.execute(SQL_RECENT_MATCHES, {"limit": limit, "tier": tier or None}).fetchall()
    champions = load_champions(conn, (c for r in rows for c in r["blue_ids"] + r["red_ids"]))
    return [MatchSummary(
        match_id=r["match_id"], game_version=r["game_version"], start_time=r["start_time"],
        duration_seconds=r["duration_seconds"], winning_team=r["winning_team"],
        seed_tier=r["seed_tier"],
        blue=tuple(champions[c] for c in r["blue_ids"]),
        red=tuple(champions[c] for c in r["red_ids"]),
    ) for r in rows]


def get_match(conn, match_id: str) -> MatchDetail | None:
    params = {"match_id": match_id}
    m = conn.execute(SQL_MATCH, params).fetchone()
    if m is None:
        return None
    participants = conn.execute(SQL_MATCH_PARTICIPANTS, params).fetchall()
    purchases = conn.execute(SQL_MATCH_PURCHASES, params).fetchall()
    champions = load_champions(conn, (p["champion_id"] for p in participants))
    items = load_items(conn, (e["item_id"] for e in purchases))
    by_participant: dict[int, list[PurchaseRef]] = {}
    for e in purchases:
        by_participant.setdefault(e["participant_number"], []).append(
            PurchaseRef(event_number=e["event_number"], game_time_ms=e["game_time_ms"],
                        item=items[e["item_id"]]))
    details = tuple(ParticipantDetail(
        participant_number=p["participant_number"], team=p["team"], role=p["role"],
        champion=champions[p["champion_id"]], won=p["won"],
        physical_damage=p["physical_damage"], magic_damage=p["magic_damage"],
        true_damage=p["true_damage"], healing_done=p["healing_done"],
        cc_seconds=p["cc_seconds"],
        purchases=tuple(by_participant.get(p["participant_number"], ())),
    ) for p in participants)
    return MatchDetail(participants=details, **m)


def _opt(value) -> float | None:
    return float(value) if value is not None else None


def _role_stats(role: str, result: dict, profile: dict | None) -> RoleStats:
    games, wins = int(result["games"]), int(result["wins"])
    has_profile = profile is not None and profile["champion_games"] > 0
    source = profile["source"] if has_profile else "none"
    pick = (lambda k: _opt(profile[k])) if has_profile else (lambda k: None)
    return RoleStats(role=role, games=games, wins=wins, win_rate=rate(wins, games),
                     physical_pm=pick("physical_pm"), magic_pm=pick("magic_pm"),
                     true_pm=pick("true_pm"), healing_pm=pick("healing_pm"),
                     cc_pm=pick("cc_pm"), profile_source=source)


def champion_overview(conn, champion_id: int) -> ChampionOverview:
    """Games and win rate by role, threat profile per role, most-played lane opponents."""
    champion = load_champions(conn, (champion_id,)).get(champion_id)
    if champion is None:
        raise ValidationError(f"Unknown champion id {champion_id}.")
    params = {"champion_id": champion_id, "limit": TOP_MATCHUPS}
    results = {r["role"]: r for r in conn.execute(SQL_CHAMPION_ROLE_RESULTS, params).fetchall()}
    profiles = {r["role"]: r for r in conn.execute(SQL_CHAMPION_PROFILES, params).fetchall()}
    by_role = tuple(_role_stats(role, results[role], profiles.get(role))
                    for role in ROLES if role in results)
    matchup_rows = conn.execute(SQL_TOP_MATCHUPS, params).fetchall()
    opponents = load_champions(conn, (r["opponent_champion_id"] for r in matchup_rows))
    matchups = tuple(MatchupStat(
        opponent=opponents[r["opponent_champion_id"]], role=r["role"], games=int(r["games"]),
        wins=int(r["wins"]), win_rate=rate(int(r["wins"]), int(r["games"])),
    ) for r in matchup_rows)
    games = sum(r.games for r in by_role)
    wins = sum(r.wins for r in by_role)
    return ChampionOverview(champion=champion, games=games, wins=wins,
                            win_rate=rate(wins, games), by_role=by_role, top_matchups=matchups)
