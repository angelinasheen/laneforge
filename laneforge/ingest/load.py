"""Load raw match/timeline files into match, participant and purchase_event.

One transaction per match; purchase events go in with COPY. Matches already
in the database are skipped, so loading is idempotent and resumable. Every
skipped match is logged at INFO with its reason.
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import AbstractSet, Mapping

import psycopg

from laneforge.ingest.admission import admit
from laneforge.ingest.rows import MatchRows, RowError, build_rows
from laneforge.ingest.storage import (
    meta_path,
    raw_match_ids,
    read_gz_json,
    read_json,
    timeline_path,
    match_path,
)

log = logging.getLogger(__name__)

UNKNOWN_TIER = "UNKNOWN"
OUTCOME_LOADED = "loaded"
OUTCOME_PRESENT = "already_present"
REASON_NO_TIMELINE = "missing_timeline"
REASON_UNREADABLE = "unreadable_file"
REASON_CONSTRAINT = "constraint_violation"

INSERT_MATCH = """
INSERT INTO match (match_id, game_version, start_time, duration_seconds, winning_team, seed_tier)
VALUES (%(match_id)s, %(game_version)s, %(start_time)s, %(duration_seconds)s,
        %(winning_team)s, %(seed_tier)s)
"""
INSERT_PARTICIPANT = """
INSERT INTO participant (match_id, participant_number, team, role, champion_id,
                         physical_damage, magic_damage, true_damage, healing_done, cc_seconds)
VALUES (%(match_id)s, %(participant_number)s, %(team)s, %(role)s, %(champion_id)s,
        %(physical_damage)s, %(magic_damage)s, %(true_damage)s, %(healing_done)s, %(cc_seconds)s)
"""
COPY_PURCHASES = (
    "COPY purchase_event (match_id, participant_number, event_number, game_time_ms, item_id) "
    "FROM STDIN"
)


@dataclass(frozen=True)
class LoadReport:
    loaded: int = 0
    already_present: int = 0
    skipped_by_reason: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def skipped(self) -> int:
        return sum(self.skipped_by_reason.values())


def load_raw_dir(
    conn: psycopg.Connection,
    raw_dir: Path,
    patch: str,
    catalogue_completed_ids: AbstractSet[int],
    *,
    limit: int | None = None,
) -> LoadReport:
    """Load every raw match in `raw_dir` not yet in the database.

    `catalogue_completed_ids` is the completed-item set from item.json (see
    completion.py); it is intersected with the `item` table so every stored
    event satisfies the foreign key. Commits any transaction the caller has
    open, then commits once per match.
    """
    conn.commit()
    present = _column_set(conn, "SELECT match_id AS v FROM match")
    champions = _column_set(conn, "SELECT champion_id AS v FROM champion")
    storable = frozenset(catalogue_completed_ids) & _column_set(conn, "SELECT item_id AS v FROM item")
    conn.commit()
    if not champions:
        raise RuntimeError("champion table is empty; run `python -m laneforge.ingest ddragon` first")
    outcomes: Counter[str] = Counter()
    for match_id in raw_match_ids(raw_dir):
        if limit is not None and outcomes[OUTCOME_LOADED] >= limit:
            break
        if match_id in present:
            outcomes[OUTCOME_PRESENT] += 1
            continue
        outcomes[_load_one(conn, raw_dir, match_id, patch, storable, champions)] += 1
    return _report(outcomes)


def _report(outcomes: Counter[str]) -> LoadReport:
    skipped = {k: v for k, v in sorted(outcomes.items()) if k not in (OUTCOME_LOADED, OUTCOME_PRESENT)}
    return LoadReport(outcomes[OUTCOME_LOADED], outcomes[OUTCOME_PRESENT], MappingProxyType(skipped))


def _column_set(conn: psycopg.Connection, sql: str) -> frozenset:
    with conn.cursor() as cur:
        cur.execute(sql)
        return frozenset(row["v"] if isinstance(row, dict) else row[0] for row in cur.fetchall())


def _load_one(conn, raw_dir: Path, match_id: str, patch: str,
              storable: frozenset[int], champions: frozenset[int]) -> str:
    try:
        match_json = read_gz_json(match_path(raw_dir, match_id))
    except (OSError, ValueError) as exc:
        return _skip(match_id, REASON_UNREADABLE, str(exc))
    rejection = admit(match_json, patch)
    if rejection is not None:
        return _skip(match_id, rejection.reason, rejection.detail)
    if not timeline_path(raw_dir, match_id).exists():
        return _skip(match_id, REASON_NO_TIMELINE, "no timeline file")
    try:
        timeline_json = read_gz_json(timeline_path(raw_dir, match_id))
        rows = build_rows(match_id, match_json, timeline_json, _seed_tier(raw_dir, match_id),
                          storable, champions)
    except RowError as exc:
        return _skip(match_id, exc.reason, str(exc))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return _skip(match_id, REASON_UNREADABLE, f"{type(exc).__name__}: {exc}")
    try:
        _insert(conn, rows)
    except (psycopg.errors.IntegrityError, psycopg.errors.DataError) as exc:
        return _skip(match_id, REASON_CONSTRAINT, str(exc).splitlines()[0])
    log.debug("loaded %s (%d purchase events)", match_id, len(rows.purchases))
    return OUTCOME_LOADED


def _skip(match_id: str, reason: str, detail: str) -> str:
    log.info("skip %s: %s (%s)", match_id, reason, detail)
    return reason


def _seed_tier(raw_dir: Path, match_id: str) -> str:
    path = meta_path(raw_dir, match_id)
    if not path.exists():
        return UNKNOWN_TIER
    tier = read_json(path).get("seed_tier")
    return str(tier) if tier else UNKNOWN_TIER


def _insert(conn: psycopg.Connection, rows: MatchRows) -> None:
    """All rows of one match, atomically."""
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(INSERT_MATCH, dict(rows.match))
            cur.executemany(INSERT_PARTICIPANT, [dict(p) for p in rows.participants])
            with cur.copy(COPY_PURCHASES) as copy:
                for row in rows.purchases:
                    copy.write_row(row)
