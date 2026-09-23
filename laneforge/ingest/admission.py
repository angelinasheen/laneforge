"""Match admission rules (draft 4 "Match admission").

A match is loaded only if it is ranked solo/duo on the loaded patch, nobody
ended it by early surrender (remake), it lasted at least 16 minutes, and each
team's five teamPosition values are exactly the five roles.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

ROLES = frozenset({"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"})
TEAMS = (100, 200)
RANKED_SOLO_QUEUE_ID = 420
MIN_DURATION_S = 16 * 60
PARTICIPANTS_PER_MATCH = 10
MS_PER_S = 1000

REASON_MALFORMED = "malformed"
REASON_QUEUE = "wrong_queue"
REASON_PATCH = "wrong_patch"
REASON_REMAKE = "early_surrender"
REASON_SHORT = "too_short"
REASON_POSITIONS = "bad_positions"


@dataclass(frozen=True)
class Rejection:
    reason: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}" if self.detail else self.reason


def patch_from_version(ddragon_version: str) -> str:
    """'16.18.1' -> '16.18'."""
    parts = ddragon_version.strip().split(".")
    if len(parts) < 2 or not all(p.isdigit() for p in parts[:2]):
        raise ValueError(f"not a Data Dragon version: {ddragon_version!r}")
    return f"{parts[0]}.{parts[1]}"


def duration_seconds(info: Mapping[str, Any]) -> int:
    """match-v5 reports gameDuration in seconds when gameEndTimestamp is
    present (patch 11.20+) and in milliseconds before that."""
    raw = int(info["gameDuration"])
    return raw if "gameEndTimestamp" in info else raw // MS_PER_S


def admit(match_json: Mapping[str, Any], patch: str) -> Rejection | None:
    """None when the match may be loaded, else the first rule it breaks."""
    info = match_json.get("info") if isinstance(match_json, Mapping) else None
    if not isinstance(info, Mapping):
        return Rejection(REASON_MALFORMED, "no info object")
    participants = info.get("participants")
    if not isinstance(participants, list) or len(participants) != PARTICIPANTS_PER_MATCH:
        return Rejection(REASON_MALFORMED, "expected ten participants")
    if info.get("queueId") != RANKED_SOLO_QUEUE_ID:
        return Rejection(REASON_QUEUE, f"queueId {info.get('queueId')}")
    version = str(info.get("gameVersion", ""))
    if not version.startswith(patch + "."):
        return Rejection(REASON_PATCH, f"gameVersion {version} is not patch {patch}")
    if any(p.get("gameEndedInEarlySurrender") for p in participants):
        return Rejection(REASON_REMAKE, "remake")
    try:
        seconds = duration_seconds(info)
    except (KeyError, TypeError, ValueError):
        return Rejection(REASON_MALFORMED, "no usable gameDuration")
    if seconds < MIN_DURATION_S:
        return Rejection(REASON_SHORT, f"{seconds}s < {MIN_DURATION_S}s")
    return _check_positions(participants)


def _check_positions(participants: list[Mapping[str, Any]]) -> Rejection | None:
    for team in TEAMS:
        positions = [p.get("teamPosition") for p in participants if p.get("teamId") == team]
        if len(positions) != len(ROLES) or set(positions) != ROLES:
            return Rejection(REASON_POSITIONS, f"team {team} positions {sorted(map(str, positions))}")
    return None
