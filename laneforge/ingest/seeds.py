"""Seed players from league-v4 entries for a fixed tier band (draft 4 "Seeding").

Seeds are appended to `data/seeds.jsonl` as {"puuid","tier","division"},
deduplicated by puuid. Pages already fetched are listed in
`data/seeds.progress.json`, so a rerun (for example after a key expiry)
continues where it stopped instead of spending requests again.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

from laneforge.ingest.riot import league_entries_url
from laneforge.ingest.storage import DataPaths, read_json, write_json_atomic

log = logging.getLogger(__name__)

DEFAULT_TIERS = ("GOLD", "PLATINUM")
DEFAULT_DIVISIONS = ("IV", "III", "II", "I")
DEFAULT_MAX_PAGES = 5
FIRST_PAGE = 1


class JsonClient(Protocol):
    def get_json(self, url: str, params: dict | None = None) -> Any: ...


@dataclass(frozen=True)
class Seed:
    puuid: str
    tier: str
    division: str


@dataclass(frozen=True)
class SeedReport:
    added: int
    total: int
    pages_fetched: int


def read_seeds(seeds_path: Path) -> tuple[Seed, ...]:
    """All seeds in file order. Blank lines are ignored; a malformed line is an error."""
    if not seeds_path.exists():
        return ()
    seeds = []
    for number, line in enumerate(seeds_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            seeds.append(Seed(puuid=row["puuid"], tier=row["tier"], division=row["division"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"{seeds_path}:{number}: malformed seed line ({exc})") from exc
    return tuple(seeds)


def collect_seeds(
    client: JsonClient,
    paths: DataPaths,
    tiers: Iterable[str] = DEFAULT_TIERS,
    divisions: Iterable[str] = DEFAULT_DIVISIONS,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> SeedReport:
    """Fetch up to `max_pages` pages per (tier, division) and append new seeds."""
    known = frozenset(seed.puuid for seed in read_seeds(paths.seeds))
    done = _load_progress(paths.seeds_progress)
    added = 0
    pages = 0
    for tier in tiers:
        for division in divisions:
            for page in range(FIRST_PAGE, max_pages + 1):
                key = _page_key(tier, division, page)
                if _band_exhausted(done, tier, division) or key in done:
                    continue
                entries = client.get_json(league_entries_url(tier, division), {"page": page})
                pages += 1
                fresh = _new_seeds(entries, tier, division, known)
                _append_seeds(paths.seeds, fresh)
                known = known | {seed.puuid for seed in fresh}
                added += len(fresh)
                marker = key if entries else _exhausted_key(tier, division)
                done = done | {marker}
                write_json_atomic(paths.seeds_progress, sorted(done))
                log.info("%s %s page %d: %d entries, %d new seeds",
                         tier, division, page, len(entries), len(fresh))
    return SeedReport(added=added, total=len(known), pages_fetched=pages)


def _new_seeds(entries: Any, tier: str, division: str, known: frozenset[str]) -> tuple[Seed, ...]:
    if not isinstance(entries, list):
        raise ValueError(f"league-v4 returned {type(entries).__name__}, expected a list")
    fresh: dict[str, Seed] = {}
    for entry in entries:
        puuid = entry.get("puuid") if isinstance(entry, dict) else None
        if not puuid:
            log.warning("league entry without puuid skipped: %r", entry)
            continue
        if puuid in known or puuid in fresh:
            continue
        fresh[puuid] = Seed(puuid=puuid, tier=entry.get("tier", tier),
                            division=entry.get("rank", division))
    return tuple(fresh.values())


def _append_seeds(seeds_path: Path, seeds: tuple[Seed, ...]) -> None:
    if not seeds:
        return
    seeds_path.parent.mkdir(parents=True, exist_ok=True)
    lines = "".join(
        json.dumps({"puuid": s.puuid, "tier": s.tier, "division": s.division}) + "\n"
        for s in seeds
    )
    with seeds_path.open("a", encoding="utf-8") as handle:
        handle.write(lines)


def _load_progress(path: Path) -> frozenset[str]:
    if not path.exists():
        return frozenset()
    raw = read_json(path)
    if not isinstance(raw, list):
        raise ValueError(f"{path} should hold a JSON list of page keys")
    return frozenset(str(item) for item in raw)


def _page_key(tier: str, division: str, page: int) -> str:
    return f"{tier}/{division}/{page}"


def _exhausted_key(tier: str, division: str) -> str:
    return f"{tier}/{division}/end"


def _band_exhausted(done: frozenset[str], tier: str, division: str) -> bool:
    return _exhausted_key(tier, division) in done
