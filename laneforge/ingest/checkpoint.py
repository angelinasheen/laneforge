"""Crawl checkpoint: an immutable value saved atomically after every match.

    {
      "seed_index": 12,                  # next seed to process (file order)
      "seen": ["NA1_...", ...],          # match ids handled (fetched or skipped)
      "tier_by_match": {"NA1_...": "GOLD"},
      "counts": {"fetched": 40, "missing": 1, "requests": 95}
    }
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from laneforge.ingest.storage import write_json_atomic

log = logging.getLogger(__name__)

EMPTY_MAPPING: Mapping = MappingProxyType({})


@dataclass(frozen=True)
class Checkpoint:
    seed_index: int = 0
    seen: frozenset[str] = frozenset()
    tier_by_match: Mapping[str, str] = field(default_factory=lambda: EMPTY_MAPPING)
    counts: Mapping[str, int] = field(default_factory=lambda: EMPTY_MAPPING)

    def with_match(self, match_id: str, tier: str | None, outcome: str) -> "Checkpoint":
        """Record one handled match. `tier` is None when nothing was stored."""
        tiers = dict(self.tier_by_match)
        if tier is not None:
            tiers[match_id] = tier
        return replace(
            self,
            seen=self.seen | {match_id},
            tier_by_match=MappingProxyType(tiers),
            counts=_bump(self.counts, outcome),
        )

    def with_seed_index(self, seed_index: int) -> "Checkpoint":
        return replace(self, seed_index=seed_index)

    def with_count(self, name: str, amount: int = 1) -> "Checkpoint":
        return replace(self, counts=_bump(self.counts, name, amount))

    def to_json(self) -> dict:
        return {
            "seed_index": self.seed_index,
            "seen": sorted(self.seen),
            "tier_by_match": dict(sorted(self.tier_by_match.items())),
            "counts": dict(sorted(self.counts.items())),
        }


def _bump(counts: Mapping[str, int], name: str, amount: int = 1) -> Mapping[str, int]:
    return MappingProxyType({**counts, name: counts.get(name, 0) + amount})


def load_checkpoint(path: Path) -> Checkpoint:
    """Read the checkpoint, or start fresh if there is none. A corrupt file is an
    error (never silently reset: that would re-download everything)."""
    if not path.exists():
        return Checkpoint()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Checkpoint(
            seed_index=int(raw.get("seed_index", 0)),
            seen=frozenset(raw.get("seen", ())),
            tier_by_match=MappingProxyType(dict(raw.get("tier_by_match", {}))),
            counts=MappingProxyType({k: int(v) for k, v in raw.get("counts", {}).items()}),
        )
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"checkpoint {path} is unreadable ({exc}); fix or move it aside") from exc


def save_checkpoint(path: Path, checkpoint: Checkpoint) -> None:
    write_json_atomic(path, checkpoint.to_json())
