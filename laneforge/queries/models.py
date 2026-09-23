"""Frozen dataclasses returned by the query layer and consumed by templates.

These are the shared vocabulary between the query agent and the UI agent.
Field names here are the field names templates use. Add fields if you must;
never rename or remove one without telling the manager.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

ROLES: tuple[str, ...] = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
ROLE_LABELS: dict[str, str] = {
    "TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "Bot", "UTILITY": "Support",
}
MIN_GAMES = 30            # "sufficient data" threshold (draft 4)
MAX_ROWS_PER_LEVEL = 5
REFERENCE_LEVEL = 11      # stat model reference champion level


@dataclass(frozen=True)
class ChampionRef:
    champion_id: int
    name: str
    ddragon_key: str
    base_health: int = 0
    health_per_level: float = 0.0
    base_armor: int = 0
    armor_per_level: float = 0.0
    base_magic_resist: int = 0
    magic_resist_per_level: float = 0.0
    base_attack_damage: int = 0
    attack_damage_per_level: float = 0.0
    base_attack_speed: float = 0.0
    attack_speed_per_level_pct: float = 0.0


@dataclass(frozen=True)
class ItemRef:
    item_id: int
    name: str
    gold_cost: int
    is_legendary: bool
    is_boots: bool
    attack_damage: int = 0
    ability_power: int = 0
    health: int = 0
    mana: int = 0
    attack_speed_pct: float = 0.0
    crit_chance_pct: float = 0.0
    move_speed: int = 0
    life_steal_pct: float = 0.0
    armor: int = 0
    magic_resist: int = 0
    ability_haste: int = 0
    lethality: int = 0
    armor_pen_pct: float = 0.0
    magic_pen_flat: int = 0
    magic_pen_pct: float = 0.0
    tenacity_pct: float = 0.0
    applies_grievous_wounds: bool = False


@dataclass(frozen=True)
class UserRef:
    user_id: int
    display_name: str
    created_at: datetime


@dataclass(frozen=True)
class DatasetSummary:
    patch: str | None            # e.g. "16.18" (prefix of match.game_version), None when no matches
    region: str = "NA"
    matches: int = 0
    participants: int = 0
    tiers: tuple[str, ...] = ()  # distinct seed tiers, ordered low to high
    first_start: datetime | None = None
    last_start: datetime | None = None


# --- core builds -------------------------------------------------------------

@dataclass(frozen=True)
class BuildRow:
    items: tuple[ItemRef, ...]   # three for a sequence row, one for a per-item row
    games: int
    wins: int
    pick_rate: float             # games / sample_size, 0..1
    win_rate: float              # wins / games, 0..1 (0.0 when games == 0)
    ci_low: float
    ci_high: float
    sufficient: bool             # games >= MIN_GAMES


@dataclass(frozen=True)
class LadderAnswer:
    level: int                   # 1..4
    scope: str                   # 'matchup' | 'champion'
    granularity: str             # 'sequence' | 'item'
    sample_size: int             # participants with a full core at this scope
    label: str                   # "vs Zed, mid: 212 games" / "all opponents: 1,840 games"
    fell_back: bool              # True when level > 1
    fallback_note: str | None    # "not enough Ahri vs Zed games; showing Ahri, mid, all opponents"
    rows: tuple[BuildRow, ...]


# --- situational ------------------------------------------------------------

@dataclass(frozen=True)
class CompMember:
    champion: ChampionRef
    role: str | None             # known only for the lane opponent
    source: str                  # 'role' | 'champion' | 'none'
    physical_pm: float
    magic_pm: float
    true_pm: float
    healing_pm: float
    cc_pm: float


@dataclass(frozen=True)
class CompProfile:
    magic_share: float
    physical_share: float
    true_share: float
    healing_pm: float
    cc_pm: float
    healing_pm_p75: float | None
    cc_pm_p75: float | None
    partial: bool                # fewer than four other enemies given
    members: tuple[CompMember, ...]


@dataclass(frozen=True)
class Rule:
    key: str                     # 'magic' | 'physical' | 'healing' | 'cc'
    reason: str                  # "comp is 61% magic"
    item_class: str              # "magic resist" | "armor" | "anti-heal" | "tenacity"


@dataclass(frozen=True)
class Evidence:
    games: int
    wins: int
    win_rate: float
    ci_low: float
    ci_high: float
    condition_text: str          # "comps with at least 55% magic damage"


@dataclass(frozen=True)
class Suggestion:
    item: ItemRef
    rule: Rule
    score: float
    score_text: str              # "+1,420 effective HP per 1,000 gold" / "raises your damage to Zed by 17%"
    evidence: Evidence | None    # None => "stat model only, no sample"


@dataclass(frozen=True)
class SituationalAnswer:
    profile: CompProfile
    triggered: tuple[Rule, ...]
    suggestions: tuple[Suggestion, ...]
    class_evidence: dict[str, Evidence] = field(default_factory=dict)   # rule key -> class-level line


# --- saved builds -----------------------------------------------------------

@dataclass(frozen=True)
class SavedBuild:
    build_id: int
    user: UserRef
    name: str
    notes: str | None
    champion: ChampionRef
    role: str
    opponent: ChampionRef
    enemies: tuple[ChampionRef, ...]
    items: tuple[ItemRef, ...]   # ordered by position 1..3
    is_customized: bool
    created_at: datetime
    stat_totals: dict[str, float]   # column-wise sums over the three items, keys = ItemRef stat fields


# --- browsing ---------------------------------------------------------------

@dataclass(frozen=True)
class PurchaseRef:
    event_number: int
    game_time_ms: int
    item: ItemRef


@dataclass(frozen=True)
class ParticipantDetail:
    participant_number: int
    team: int
    role: str
    champion: ChampionRef
    won: bool
    physical_damage: int
    magic_damage: int
    true_damage: int
    healing_done: int
    cc_seconds: int
    purchases: tuple[PurchaseRef, ...]


@dataclass(frozen=True)
class MatchSummary:
    match_id: str
    game_version: str
    start_time: datetime
    duration_seconds: int
    winning_team: int
    seed_tier: str
    blue: tuple[ChampionRef, ...]   # role order
    red: tuple[ChampionRef, ...]


@dataclass(frozen=True)
class MatchDetail:
    match_id: str
    game_version: str
    start_time: datetime
    duration_seconds: int
    winning_team: int
    seed_tier: str
    participants: tuple[ParticipantDetail, ...]   # ordered by participant_number


@dataclass(frozen=True)
class RoleStats:
    role: str
    games: int
    wins: int
    win_rate: float
    physical_pm: float | None
    magic_pm: float | None
    true_pm: float | None
    healing_pm: float | None
    cc_pm: float | None
    profile_source: str          # 'role' | 'champion' | 'none'


@dataclass(frozen=True)
class MatchupStat:
    opponent: ChampionRef
    role: str
    games: int
    wins: int
    win_rate: float


@dataclass(frozen=True)
class ChampionOverview:
    champion: ChampionRef
    games: int
    wins: int
    win_rate: float
    by_role: tuple[RoleStats, ...]
    top_matchups: tuple[MatchupStat, ...]   # most-played lane opponents, up to 8


# --- item usage (added by the query agent for /items/<id>) -------------------

@dataclass(frozen=True)
class ItemChampionUse:
    champion: ChampionRef
    role: str
    games: int                   # full-core participants whose first three include the item
    wins: int
    win_rate: float


@dataclass(frozen=True)
class ItemUsage:
    item: ItemRef
    completions: int             # purchase events for this item (all participants)
    core_games: int              # full-core participants whose first three include it
    core_wins: int
    core_win_rate: float
    saved_builds: int            # saved builds that contain it
    top_champions: tuple[ItemChampionUse, ...]   # up to 10, by games desc
