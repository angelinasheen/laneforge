"""On-disk layout of the ingest pipeline and crash-safe file writes.

    data/seeds.jsonl                      one {"puuid","tier","division"} per line
    data/seeds.progress.json              league-v4 pages already collected
    data/checkpoint.json                  crawl cursor, seen match ids, counts
    data/raw/{match_id}.json.gz           match-v5 match response
    data/raw/{match_id}.timeline.json.gz  match-v5 timeline response
    data/raw/{match_id}.meta.json         {"seed_tier": "GOLD"}
"""
from __future__ import annotations

import gzip
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from laneforge.db import REPO_ROOT

DEFAULT_DATA_DIR = REPO_ROOT / "data"

MATCH_SUFFIX = ".json.gz"
TIMELINE_SUFFIX = ".timeline.json.gz"
META_SUFFIX = ".meta.json"


@dataclass(frozen=True)
class DataPaths:
    root: Path

    @property
    def seeds(self) -> Path:
        return self.root / "seeds.jsonl"

    @property
    def seeds_progress(self) -> Path:
        return self.root / "seeds.progress.json"

    @property
    def checkpoint(self) -> Path:
        return self.root / "checkpoint.json"

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def ddragon(self) -> Path:
        return self.root / "ddragon"


def match_path(raw_dir: Path, match_id: str) -> Path:
    return raw_dir / f"{match_id}{MATCH_SUFFIX}"


def timeline_path(raw_dir: Path, match_id: str) -> Path:
    return raw_dir / f"{match_id}{TIMELINE_SUFFIX}"


def meta_path(raw_dir: Path, match_id: str) -> Path:
    return raw_dir / f"{match_id}{META_SUFFIX}"


def raw_match_ids(raw_dir: Path) -> tuple[str, ...]:
    """Match ids with a match file on disk, sorted."""
    if not raw_dir.is_dir():
        return ()
    ids = (
        p.name[: -len(MATCH_SUFFIX)]
        for p in raw_dir.iterdir()
        if p.name.endswith(MATCH_SUFFIX) and not p.name.endswith(TIMELINE_SUFFIX)
    )
    return tuple(sorted(ids))


def has_raw_pair(raw_dir: Path, match_id: str) -> bool:
    return match_path(raw_dir, match_id).exists() and timeline_path(raw_dir, match_id).exists()


def write_bytes_atomic(path: Path, payload: bytes) -> None:
    """Write to a temp file in the same directory, fsync, then rename over `path`.
    A crash leaves either the old file or the new one, never half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def write_json_atomic(path: Path, obj: Any) -> None:
    write_bytes_atomic(path, json.dumps(obj, indent=None, separators=(",", ":")).encode())


def write_gz_json_atomic(path: Path, obj: Any) -> None:
    write_bytes_atomic(path, gzip.compress(json.dumps(obj).encode()))


def read_gz_json(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
