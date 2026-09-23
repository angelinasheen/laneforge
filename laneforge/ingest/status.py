"""`python -m laneforge.ingest status`: where the pipeline stands."""
from __future__ import annotations

import logging
import os
from typing import Callable

import psycopg

from laneforge import db
from laneforge.ingest.checkpoint import load_checkpoint
from laneforge.ingest.ratelimit import RateLimiter
from laneforge.ingest.riot import RiotAuthError, RiotClient, RiotError, league_entries_url
from laneforge.ingest.seeds import read_seeds
from laneforge.ingest.storage import DataPaths, raw_match_ids

log = logging.getLogger(__name__)

COUNTED_TABLES = ("match", "participant", "purchase_event")  # constants, never input
KEY_PROBE = ("GOLD", "I")


def status_lines(paths: DataPaths, api_key: str | None,
                 connect: Callable[[], psycopg.Connection] = db.connect) -> tuple[str, ...]:
    return (
        *_file_lines(paths),
        *_db_lines(connect),
        _key_line(api_key),
    )


def _file_lines(paths: DataPaths) -> tuple[str, ...]:
    seeds = read_seeds(paths.seeds)
    checkpoint = load_checkpoint(paths.checkpoint)
    counts = ", ".join(f"{k}={v}" for k, v in sorted(checkpoint.counts.items())) or "none"
    return (
        f"seeds:            {len(seeds)} in {paths.seeds}",
        f"raw matches:      {len(raw_match_ids(paths.raw))} in {paths.raw}",
        f"crawl cursor:     seed {checkpoint.seed_index} of {len(seeds)}, "
        f"{len(checkpoint.seen)} match ids seen",
        f"checkpoint counts: {counts}",
    )


def _db_lines(connect: Callable[[], psycopg.Connection]) -> tuple[str, ...]:
    try:
        with connect() as conn:
            return tuple(
                f"rows in {table + ':':<16}{_count(conn, table)}" for table in COUNTED_TABLES
            )
    except psycopg.Error as exc:
        return (f"database:         unavailable ({str(exc).splitlines()[0]})",)


def _count(conn: psycopg.Connection, table: str) -> int:
    # `table` comes from COUNTED_TABLES, a constant; never user input.
    return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


def _key_line(api_key: str | None) -> str:
    if not api_key:
        return "riot key:         not set (RIOT_API_KEY in .env); skipped check"
    try:
        with RiotClient(api_key, RateLimiter()) as client:
            client.get_json(league_entries_url(*KEY_PROBE), {"page": 1})
        return "riot key:         valid"
    except RiotAuthError:
        return "riot key:         REJECTED (expired or invalid) - regenerate the dev key"
    except RiotError as exc:
        return f"riot key:         could not check ({exc})"


def api_key_from_env() -> str | None:
    return os.environ.get("RIOT_API_KEY") or None
