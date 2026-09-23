"""Deterministic synthetic matches for demos and UI work while the real crawl runs.

    .venv/bin/python scripts/seed_synthetic.py --matches 3000 --seed 7 --tier-band GOLD..PLATINUM

Every champion gets role pools from its Data Dragon tags, a class (ap / ad / tank)
that decides its item pool and damage mix, and a fixed favourite core of three
legendaries, so pick rates look like real data. Previous synthetic matches
(`NA1_SYN_%`) are replaced; real matches are never touched.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from laneforge import db  # noqa: E402
from laneforge.ingest.ddragon import DEFAULT_DDRAGON_DIR, load_catalogue  # noqa: E402

ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
TIERS = ("IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND",
         "MASTER", "GRANDMASTER", "CHALLENGER")
TAG_ROLES = {
    "Mage": ("MIDDLE",), "Assassin": ("MIDDLE", "JUNGLE"), "Marksman": ("BOTTOM",),
    "Support": ("UTILITY",), "Tank": ("TOP", "JUNGLE"), "Fighter": ("TOP", "JUNGLE"),
}
HEALERS = frozenset({"Aatrox", "Soraka", "Vladimir", "Sylas", "Warwick", "Yuumi", "Sona",
                     "Nami", "Swain", "DrMundo", "Briar"})
BOOTS_BY_CLASS = {"ap": (3020, 3158, 3111), "ad": (3006, 3047, 3158), "tank": (3047, 3111)}
MATCH_ID_PREFIX = "NA1_SYN_"
PRIMARY_ROLE_WEIGHT = 3.0
POPULARITY_SIGMA = 0.8
MIN_DURATION_S, MAX_DURATION_S, MODE_DURATION_S = 16 * 60, 40 * 60, 28 * 60
START = datetime(2026, 9, 10, 0, 0, 0)
SPAN_S = 7 * 86400
BUILD_VERSIONS = ("16.18.712.2150", "16.18.712.4431", "16.18.715.1098")
WIN_PROB_BOUNDS = (0.25, 0.75)
FAVOURITE_BONUS = 0.015


@dataclass(frozen=True)
class ChampionSpec:
    champion_id: int
    key: str
    roles: tuple[str, ...]
    primary_roles: tuple[str, ...]
    klass: str                         # 'ap' | 'ad' | 'tank'
    popularity: float
    favourite: tuple[int, int, int]
    alternate: tuple[int, int, int]
    pool: tuple[int, ...]
    boots: tuple[int, ...]
    damage_pm: float
    magic_share: float
    true_share: float
    healing_pm: float
    cc_pm: float


@dataclass(frozen=True)
class Catalogue:
    champions: dict[int, ChampionSpec]
    role_pools: dict[str, tuple[tuple[int, ...], tuple[float, ...]]]
    item_effect: dict[int, float]
    reactive: tuple[int, ...]


@dataclass(frozen=True)
class SeedSummary:
    matches: int
    participants: int
    purchase_events: int
    seconds: float


# --- catalogue ------------------------------------------------------------------

def _tags_by_key(ddragon_dir: Path) -> dict[str, dict]:
    data = json.loads((ddragon_dir / "champion.json").read_text())["data"]
    return {c["id"]: c for c in data.values()}


def _klass(raw: dict) -> str:
    tags = raw["tags"]
    if tags[0] == "Tank" or (tags[0] == "Support" and "Tank" in tags):
        return "tank"
    return "ap" if raw["info"]["magic"] > raw["info"]["attack"] else "ad"


def _item_pools(conn) -> dict[str, tuple[int, ...]]:
    rows = conn.execute(
        "SELECT item_id, ability_power, attack_damage, lethality, armor, magic_resist, health, "
        "applies_grievous_wounds FROM item WHERE is_legendary AND NOT is_boots ORDER BY item_id"
    ).fetchall()
    return {
        "ap": tuple(r["item_id"] for r in rows if r["ability_power"] > 0),
        "ad": tuple(r["item_id"] for r in rows
                    if (r["attack_damage"] > 0 or r["lethality"] > 0) and r["ability_power"] == 0),
        "tank": tuple(r["item_id"] for r in rows
                      if (r["armor"] > 0 or r["magic_resist"] > 0 or r["health"] > 0)
                      and r["ability_power"] == 0 and r["attack_damage"] == 0),
        "reactive": tuple(r["item_id"] for r in rows
                          if r["armor"] > 0 or r["magic_resist"] > 0
                          or r["applies_grievous_wounds"]),
    }


def _boots(conn) -> tuple[int, ...]:
    rows = conn.execute("SELECT item_id FROM item WHERE is_boots AND gold_cost >= 900 "
                        "ORDER BY item_id").fetchall()
    return tuple(r["item_id"] for r in rows)


def _champion_spec(rng: random.Random, row: dict, raw: dict, pools: dict, boots: tuple) -> ChampionSpec:
    tags = raw["tags"]
    klass = _klass(raw)
    roles = tuple(r for r in ROLES if any(r in TAG_ROLES.get(t, ()) for t in tags)) or ("TOP",)
    pool = pools[klass]
    favourite = tuple(rng.sample(pool, 3))
    alternate = (favourite[0],) + tuple(rng.sample([i for i in pool if i not in favourite], 2))
    own_boots = tuple(b for b in BOOTS_BY_CLASS[klass] if b in boots) or boots
    magic = {"ap": rng.uniform(0.72, 0.88), "ad": rng.uniform(0.04, 0.12),
             "tank": rng.uniform(0.3, 0.6)}[klass]
    support_like = klass == "tank" or "Support" in tags
    return ChampionSpec(
        champion_id=row["champion_id"], key=row["ddragon_key"], roles=roles,
        primary_roles=TAG_ROLES.get(tags[0], roles), klass=klass,
        popularity=rng.lognormvariate(0.0, POPULARITY_SIGMA),
        favourite=favourite, alternate=alternate,
        pool=pool, boots=own_boots,
        damage_pm=rng.uniform(550, 850) * (0.7 if klass == "tank" else 1.0),
        magic_share=magic, true_share=rng.uniform(0.02, 0.07),
        healing_pm=rng.uniform(500, 900) if row["ddragon_key"] in HEALERS else rng.uniform(40, 200),
        cc_pm=rng.uniform(1.5, 3.0) if support_like else rng.uniform(0.3, 1.2),
    )


def build_catalogue(conn, seed: int, ddragon_dir: Path = DEFAULT_DDRAGON_DIR) -> Catalogue:
    rng = random.Random(seed)
    raw_by_key = _tags_by_key(ddragon_dir)
    pools, boots = _item_pools(conn), _boots(conn)
    rows = conn.execute("SELECT champion_id, ddragon_key FROM champion ORDER BY champion_id").fetchall()
    specs = {r["champion_id"]: _champion_spec(rng, r, raw_by_key[r["ddragon_key"]], pools, boots)
             for r in rows if r["ddragon_key"] in raw_by_key}
    role_pools = {}
    for role in ROLES:
        members = [s for s in specs.values() if role in s.roles]
        weights = [s.popularity * (PRIMARY_ROLE_WEIGHT if role in s.primary_roles else 1.0)
                   for s in members]
        role_pools = {**role_pools, role: (tuple(s.champion_id for s in members), tuple(weights))}
    all_items = sorted({i for p in pools.values() for i in p})
    effects = {i: rng.uniform(-0.04, 0.04) for i in all_items}
    return Catalogue(specs, role_pools, effects, pools["reactive"])


# --- one match ----------------------------------------------------------------------

def _roster(rng: random.Random, cat: Catalogue) -> tuple[tuple[int, str, int], ...]:
    """Ten (team, role, champion_id) in participant-number order."""
    taken: set[int] = set()
    picks = []
    for team in (100, 200):
        for role in ROLES:
            ids, weights = cat.role_pools[role]
            choice = rng.choices(ids, weights)[0]
            while choice in taken:
                choice = rng.choices(ids, weights)[0]
            taken = taken | {choice}
            picks.append((team, role, choice))
    return tuple(picks)


def _core(rng: random.Random, spec: ChampionSpec) -> tuple[int, int, int]:
    draw = rng.random()
    if draw < 0.33:
        return spec.favourite
    if draw < 0.48:
        return spec.alternate
    if draw < 0.60:
        a, b, c = spec.favourite
        return (a, c, b)
    first = spec.favourite[0] if rng.random() < 0.5 else rng.choice(spec.pool)
    rest = rng.sample([i for i in spec.pool if i != first], 2)
    return (first, rest[0], rest[1])


def _legendaries(rng: random.Random, spec: ChampionSpec, cat: Catalogue, duration: int) -> tuple[int, ...]:
    core = _core(rng, spec)
    extra_slots = min(rng.randint(0, 3), max(0, (duration - MIN_DURATION_S) // 360))
    extras: list[int] = []
    for _ in range(extra_slots):
        source = cat.reactive if rng.random() < 0.3 else spec.pool
        candidates = [i for i in source if i not in core and i not in extras]
        extras.append(rng.choice(candidates))
    return core + tuple(extras)


def _purchase_times(rng: random.Random, count: int, duration: int) -> tuple[int, ...]:
    """Game times in ms: boots, then `count` legendaries, ascending and inside the game."""
    first = int(min(rng.uniform(600, 840), duration * 0.55))
    boots = rng.randint(300, max(301, min(first - 30, 700)))
    last = duration - 20
    seconds = [boots, first] + [rng.randint(first + 60, last) for _ in range(count - 1)]
    return tuple(sorted(s * 1000 + rng.randint(0, 999) for s in seconds))


def _measures(rng: random.Random, spec: ChampionSpec, role: str, minutes: float) -> tuple[int, ...]:
    scale = (0.55 if role == "UTILITY" else 1.0) * rng.uniform(0.65, 1.35) * minutes
    total = spec.damage_pm * scale
    magic = total * spec.magic_share
    true = total * spec.true_share
    physical = max(0.0, total - magic - true)
    healing = spec.healing_pm * minutes * rng.uniform(0.6, 1.4)
    cc = spec.cc_pm * minutes * rng.uniform(0.5, 1.5)
    return tuple(int(round(v)) for v in (physical, magic, true, healing, cc))


def _win_probability(cores: list[tuple[int, ...]], roster, cat: Catalogue) -> float:
    edge = 0.0
    for (team, _, champion_id), core in zip(roster, cores):
        spec = cat.champions[champion_id]
        value = sum(cat.item_effect.get(i, 0.0) for i in core[:3]) / 3
        value += FAVOURITE_BONUS if core[:3] == spec.favourite else 0.0
        edge += value if team == 100 else -value
    low, high = WIN_PROB_BOUNDS
    return min(high, max(low, 0.5 + edge))


def generate_match(rng: random.Random, cat: Catalogue, n: int, total: int, tiers: tuple[str, ...]):
    """Return (match row, participant rows, purchase rows) as tuples for COPY."""
    match_id = f"{MATCH_ID_PREFIX}{n}"
    duration = int(rng.triangular(MIN_DURATION_S, MAX_DURATION_S, MODE_DURATION_S))
    minutes = duration / 60
    roster = _roster(rng, cat)
    participants, purchases, cores = [], [], []
    for number, (team, role, champion_id) in enumerate(roster, start=1):
        spec = cat.champions[champion_id]
        participants.append((match_id, number, team, role, champion_id,
                             *_measures(rng, spec, role, minutes)))
        items = _legendaries(rng, spec, cat, duration)
        times = _purchase_times(rng, len(items), duration)
        bought = (rng.choice(spec.boots),) + items
        purchases.extend((match_id, number, event, ms, item)
                         for event, (ms, item) in enumerate(zip(times, bought), start=1))
        cores.append(items)
    winner = 100 if rng.random() < _win_probability(cores, roster, cat) else 200
    start = START + timedelta(seconds=int(SPAN_S * n / max(total, 1)) + rng.randint(0, 900))
    match = (match_id, rng.choice(BUILD_VERSIONS), start, duration, winner, rng.choice(tiers))
    return match, participants, purchases


# --- loading --------------------------------------------------------------------------

def _copy(cur, table: str, columns: str, rows) -> None:
    with cur.copy(f"COPY {table} ({columns}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)


def tier_band(text: str) -> tuple[str, ...]:
    low, _, high = text.upper().partition("..")
    high = high or low
    if low not in TIERS or high not in TIERS or TIERS.index(low) > TIERS.index(high):
        raise ValueError(f"bad tier band {text!r}; expected e.g. GOLD..PLATINUM")
    return TIERS[TIERS.index(low): TIERS.index(high) + 1]


def seed(conn, matches: int, seed_value: int = 7, tiers: tuple[str, ...] = ("GOLD", "PLATINUM"),
         refresh: bool = True) -> SeedSummary:
    if matches < 1:
        raise ValueError("matches must be at least 1")
    started = time.perf_counter()
    if conn.execute("SELECT COUNT(*) AS n FROM champion").fetchone()["n"] == 0:
        load_catalogue(conn)
    cat = build_catalogue(conn, seed_value)
    rng = random.Random(seed_value)
    generated = [generate_match(rng, cat, n, matches, tiers) for n in range(1, matches + 1)]
    participant_rows = [p for _, ps, _ in generated for p in ps]
    purchase_rows = [e for _, _, es in generated for e in es]
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM match WHERE match_id LIKE %(p)s", {"p": MATCH_ID_PREFIX + "%"})
        _copy(cur, "match", "match_id, game_version, start_time, duration_seconds, winning_team, "
              "seed_tier", (m for m, _, _ in generated))
        _copy(cur, "participant", "match_id, participant_number, team, role, champion_id, "
              "physical_damage, magic_damage, true_damage, healing_done, cc_seconds",
              participant_rows)
        _copy(cur, "purchase_event", "match_id, participant_number, event_number, game_time_ms, "
              "item_id", purchase_rows)
    if refresh:
        db.refresh_views(conn)
    return SeedSummary(matches, len(participant_rows), len(purchase_rows),
                       time.perf_counter() - started)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--matches", type=int, default=3000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--tier-band", default="GOLD..PLATINUM")
    args = parser.parse_args(argv)
    try:
        tiers = tier_band(args.tier_band)
        with db.connect() as conn:
            summary = seed(conn, args.matches, args.seed, tiers)
    except (ValueError, OSError) as exc:
        print(f"seed_synthetic: {exc}", file=sys.stderr)
        return 1
    print(f"seeded {summary.matches:,} synthetic matches ({'/'.join(tiers)}): "
          f"{summary.participants:,} participants, {summary.purchase_events:,} purchase events "
          f"in {summary.seconds:.1f} s; views refreshed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
