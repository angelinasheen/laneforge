"""Turn one admitted match (+ timeline) into database rows. Pure: no I/O."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AbstractSet, Any, Mapping

from laneforge.ingest.admission import duration_seconds
from laneforge.ingest.timeline import item_events_by_participant, replay_purchases

log = logging.getLogger(__name__)

MS_PER_S = 1000

# participant column -> match-v5 participant field
MEASURE_FIELDS = (
    ("physical_damage", "physicalDamageDealtToChampions"),
    ("magic_damage", "magicDamageDealtToChampions"),
    ("true_damage", "trueDamageDealtToChampions"),
    ("healing_done", "totalHeal"),
    ("cc_seconds", "timeCCingOthers"),
)


class RowError(ValueError):
    """The payload cannot be turned into rows; `reason` is a short skip key."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


@dataclass(frozen=True)
class MatchRows:
    match: Mapping[str, Any]
    participants: tuple[Mapping[str, Any], ...]
    purchases: tuple[tuple[str, int, int, int, int], ...]   # COPY column order


def build_rows(
    match_id: str,
    match_json: Mapping[str, Any],
    timeline_json: Mapping[str, Any],
    seed_tier: str,
    storable_item_ids: AbstractSet[int],
    known_champion_ids: AbstractSet[int],
) -> MatchRows:
    info = match_json["info"]
    payload_id = match_json.get("metadata", {}).get("matchId")
    if payload_id != match_id:
        raise RowError("id_mismatch", f"file {match_id} holds {payload_id}")
    seconds = duration_seconds(info)
    match_row = {
        "match_id": match_id,
        "game_version": str(info["gameVersion"]),
        "start_time": _utc_naive(int(info["gameStartTimestamp"])),
        "duration_seconds": seconds,
        "winning_team": _winning_team(info),
        "seed_tier": seed_tier,
    }
    participants = tuple(_participant_row(match_id, p, known_champion_ids)
                         for p in info["participants"])
    numbers = frozenset(p["participant_number"] for p in participants)
    purchases = _purchase_rows(match_id, timeline_json, storable_item_ids, seconds * MS_PER_S,
                               numbers)
    return MatchRows(match_row, participants, purchases)


def _utc_naive(epoch_ms: int) -> datetime:
    return datetime.fromtimestamp(epoch_ms / MS_PER_S, tz=timezone.utc).replace(tzinfo=None)


def _winning_team(info: Mapping[str, Any]) -> int:
    winners = [t["teamId"] for t in info.get("teams", ()) if t.get("win") is True]
    if len(winners) != 1:
        raise RowError("no_winner", f"teams report winners {winners}")
    return int(winners[0])


def _participant_row(match_id: str, p: Mapping[str, Any],
                     known_champion_ids: AbstractSet[int]) -> Mapping[str, Any]:
    champion_id = int(p["championId"])
    if champion_id not in known_champion_ids:
        raise RowError("unknown_champion", f"champion {champion_id} is not in the catalogue")
    row = {
        "match_id": match_id,
        "participant_number": int(p["participantId"]),
        "team": int(p["teamId"]),
        "role": p["teamPosition"],
        "champion_id": champion_id,
    }
    return {**row, **{column: int(p[field]) for column, field in MEASURE_FIELDS}}


def _purchase_rows(match_id: str, timeline_json: Mapping[str, Any],
                   storable_item_ids: AbstractSet[int], duration_ms: int,
                   participant_numbers: AbstractSet[int]):
    rows = []
    for participant_number, events in sorted(item_events_by_participant(timeline_json).items()):
        if participant_number not in participant_numbers:
            # Real timelines carry a few item events with participantId 0
            # (e.g. 3865 at t=0); they belong to no player.
            log.debug("%s: ignoring %d item events for participantId %d",
                      match_id, len(events), participant_number)
            continue
        in_game = tuple(e for e in events if int(e["timestamp"]) <= duration_ms)
        if len(in_game) != len(events):
            log.debug("%s p%d: dropped %d item events after game end",
                      match_id, participant_number, len(events) - len(in_game))
        for purchase in replay_purchases(in_game, storable_item_ids.__contains__):
            rows.append((match_id, participant_number, purchase.event_number,
                         purchase.game_time_ms, purchase.item_id))
    return tuple(rows)
