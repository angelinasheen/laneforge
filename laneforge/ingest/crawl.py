"""Download match + timeline JSON for every seed's ranked games in a time window.

Draft 4 "Seeding, not crawling": seeds come from league-v4; we never expand
from co-participants. Every handled match id is recorded in the checkpoint
immediately, so the crawl can be killed (Ctrl-C, expired key) at any point
and resumed with the same command.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from laneforge.ingest.checkpoint import Checkpoint, load_checkpoint, save_checkpoint
from laneforge.ingest.riot import (
    RANKED_SOLO_QUEUE_ID,
    NotFound,
    RiotAuthError,
    RiotServerError,
    match_ids_url,
    match_url,
    timeline_url,
)
from laneforge.ingest.seeds import Seed, read_seeds
from laneforge.ingest.storage import (
    DataPaths,
    has_raw_pair,
    match_path,
    meta_path,
    timeline_path,
    write_gz_json_atomic,
    write_json_atomic,
)

log = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS = 21
MATCH_ID_PAGE_SIZE = 100  # the API maximum for `count`

STOP_DONE = "done"
STOP_LIMIT = "limit"
STOP_AUTH = "auth"
STOP_INTERRUPTED = "interrupted"
STOP_SERVER = "server_error"

OUTCOME_FETCHED = "fetched"
OUTCOME_MISSING = "missing"
OUTCOME_ON_DISK = "already_on_disk"
COUNT_ERRORS = "errors"


class JsonClient(Protocol):
    def get_json(self, url: str, params: dict | None = None) -> Any: ...


@dataclass(frozen=True)
class CrawlWindow:
    """Epoch seconds, as match-v5 `startTime` / `endTime` expect."""

    start_s: int
    end_s: int

    def __post_init__(self) -> None:
        if self.start_s >= self.end_s:
            raise ValueError("crawl window start must be before its end")


@dataclass(frozen=True)
class CrawlReport:
    fetched: int
    missing: int
    stopped: str
    message: str = ""
    seed_index: int = 0


def window_from_dates(since: date | None, until: date | None,
                      now: datetime | None = None) -> CrawlWindow:
    """UTC day bounds; defaults to the last 21 days ending now."""
    now = now or datetime.now(timezone.utc)
    end = _day_start(until) + timedelta(days=1) if until else now
    start = _day_start(since) if since else end - timedelta(days=DEFAULT_WINDOW_DAYS)
    return CrawlWindow(int(start.timestamp()), int(end.timestamp()))


def _day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def list_match_ids(client: JsonClient, puuid: str, window: CrawlWindow) -> tuple[str, ...]:
    """All ranked solo/duo match ids for `puuid` in the window, newest first."""
    ids: list[str] = []
    start = 0
    while True:
        page = client.get_json(match_ids_url(puuid), {
            "queue": RANKED_SOLO_QUEUE_ID, "type": "ranked",
            "startTime": window.start_s, "endTime": window.end_s,
            "start": start, "count": MATCH_ID_PAGE_SIZE,
        })
        if not isinstance(page, list):
            raise ValueError(f"match id list for {puuid} was {type(page).__name__}, not a list")
        ids.extend(str(match_id) for match_id in page)
        if len(page) < MATCH_ID_PAGE_SIZE:
            return tuple(dict.fromkeys(ids))
        start += MATCH_ID_PAGE_SIZE


class _Run:
    """Mutable bookkeeping for one crawl invocation (local to `crawl`)."""

    def __init__(self, checkpoint: Checkpoint, path: Path, limit: int | None) -> None:
        self.checkpoint = checkpoint
        self.path = path
        self.limit = limit
        self.fetched = 0
        self.missing = 0

    def record(self, checkpoint: Checkpoint) -> None:
        self.checkpoint = checkpoint
        save_checkpoint(self.path, checkpoint)

    def limit_reached(self) -> bool:
        return self.limit is not None and self.fetched >= self.limit

    def report(self, stopped: str, message: str = "") -> CrawlReport:
        save_checkpoint(self.path, self.checkpoint)
        return CrawlReport(self.fetched, self.missing, stopped, message,
                           self.checkpoint.seed_index)


def crawl(client: JsonClient, paths: DataPaths, window: CrawlWindow,
          limit: int | None = None) -> CrawlReport:
    """Fetch unseen matches for every seed from the checkpoint's cursor on."""
    seeds = read_seeds(paths.seeds)
    if not seeds:
        return CrawlReport(0, 0, STOP_DONE, "no seeds; run `seed` first")
    run = _Run(load_checkpoint(paths.checkpoint), paths.checkpoint, limit)
    try:
        for index in range(run.checkpoint.seed_index, len(seeds)):
            if not _crawl_seed(client, paths.raw, seeds[index], window, run):
                return run.report(STOP_LIMIT, f"stopped after {run.fetched} matches (--limit)")
            run.record(run.checkpoint.with_seed_index(index + 1))
    except RiotAuthError as exc:
        return run.report(STOP_AUTH, str(exc))
    except RiotServerError as exc:
        return run.report(STOP_SERVER, f"Riot kept failing, stopping to resume later: {exc}")
    except KeyboardInterrupt:
        return run.report(STOP_INTERRUPTED, "interrupted; checkpoint saved")
    return run.report(STOP_DONE, f"all {len(seeds)} seeds processed")


def _crawl_seed(client: JsonClient, raw_dir: Path, seed: Seed, window: CrawlWindow,
                run: _Run) -> bool:
    """Handle every unseen match of one seed. False when the run limit stops us."""
    match_ids = list_match_ids(client, seed.puuid, window)
    run.record(run.checkpoint.with_count("requests", 1))
    log.info("seed %s (%s %s): %d match ids", seed.puuid[:12], seed.tier, seed.division,
             len(match_ids))
    for match_id in match_ids:
        if match_id in run.checkpoint.seen:
            continue
        if has_raw_pair(raw_dir, match_id):
            run.record(run.checkpoint.with_match(match_id, None, OUTCOME_ON_DISK))
            continue
        if run.limit_reached():
            return False
        _fetch_one(client, raw_dir, match_id, seed.tier, run)
    return True


def _fetch_one(client: JsonClient, raw_dir: Path, match_id: str, tier: str, run: _Run) -> None:
    try:
        match = client.get_json(match_url(match_id))
        timeline = client.get_json(timeline_url(match_id))
    except NotFound as exc:
        log.info("skip %s: %s", match_id, exc)
        run.missing += 1
        run.record(run.checkpoint.with_match(match_id, None, OUTCOME_MISSING))
        return
    # meta, then timeline, then match: a match file on disk implies the rest.
    write_json_atomic(meta_path(raw_dir, match_id), {"seed_tier": tier})
    write_gz_json_atomic(timeline_path(raw_dir, match_id), timeline)
    write_gz_json_atomic(match_path(raw_dir, match_id), match)
    run.fetched += 1
    run.record(run.checkpoint.with_match(match_id, tier, OUTCOME_FETCHED).with_count("requests", 2))
    log.info("fetched %s (%s) [%d this run]", match_id, tier, run.fetched)
