"""Crawl and seed collection against a fake client and a temp data dir."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from laneforge.ingest.checkpoint import load_checkpoint
from laneforge.ingest.crawl import (
    STOP_AUTH,
    STOP_DONE,
    STOP_INTERRUPTED,
    STOP_LIMIT,
    crawl,
    window_from_dates,
)
from laneforge.ingest.riot import NotFound, RiotAuthError
from laneforge.ingest.seeds import collect_seeds, read_seeds
from laneforge.ingest.storage import DataPaths, meta_path, raw_match_ids, read_gz_json

WINDOW = window_from_dates(date(2026, 9, 1), date(2026, 9, 21))


class FakeClient:
    """Answers by URL suffix; records every URL requested."""

    def __init__(self, match_ids_by_puuid=None, missing=(), fail_on=None, league_pages=None):
        self.match_ids_by_puuid = match_ids_by_puuid or {}
        self.missing = set(missing)
        self.fail_on = fail_on or {}
        self.league_pages = league_pages or {}
        self.calls: list[tuple[str, dict]] = []

    def get_json(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        for fragment, exc in self.fail_on.items():
            if fragment in url:
                raise exc
        if "/league/v4/entries/" in url:
            tier, division = url.rsplit("/", 2)[-2:]
            return self.league_pages.get((tier, division, params["page"]), [])
        if "/by-puuid/" in url:
            puuid = url.split("/by-puuid/")[1].split("/")[0]
            ids = self.match_ids_by_puuid.get(puuid, [])
            return ids[params["start"]: params["start"] + params["count"]]
        match_id = url.split("/matches/")[1].split("/")[0]
        if match_id in self.missing and url.endswith("/timeline"):
            raise NotFound(url)
        if url.endswith("/timeline"):
            return {"metadata": {"matchId": match_id}, "info": {"frames": []}}
        return {"metadata": {"matchId": match_id}, "info": {"queueId": 420}}

    def urls(self, fragment):
        return [u for u, _ in self.calls if fragment in u]


def write_seeds(paths: DataPaths, seeds):
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.seeds.write_text("".join(json.dumps(s) + "\n" for s in seeds))


@pytest.fixture
def paths(tmp_path):
    p = DataPaths(tmp_path / "data")
    write_seeds(p, [
        {"puuid": "p-gold", "tier": "GOLD", "division": "II"},
        {"puuid": "p-plat", "tier": "PLATINUM", "division": "IV"},
    ])
    return p


def test_crawl_writes_gzipped_pairs_meta_and_checkpoint(paths):
    # Arrange
    client = FakeClient({"p-gold": ["NA1_1", "NA1_2"], "p-plat": ["NA1_2", "NA1_3"]})

    # Act
    report = crawl(client, paths, WINDOW)

    # Assert
    assert report.stopped == STOP_DONE
    assert report.fetched == 3
    assert raw_match_ids(paths.raw) == ("NA1_1", "NA1_2", "NA1_3")
    assert read_gz_json(paths.raw / "NA1_1.timeline.json.gz")["metadata"]["matchId"] == "NA1_1"
    assert json.loads(meta_path(paths.raw, "NA1_3").read_text()) == {"seed_tier": "PLATINUM"}
    checkpoint = load_checkpoint(paths.checkpoint)
    assert checkpoint.seed_index == 2
    assert checkpoint.seen == {"NA1_1", "NA1_2", "NA1_3"}
    assert checkpoint.tier_by_match["NA1_2"] == "GOLD"   # first seed that found it wins
    assert not list(paths.raw.glob("*.tmp"))


def test_match_id_listing_uses_queue_and_window(paths):
    client = FakeClient({"p-gold": []})

    crawl(client, paths, WINDOW)

    _, params = client.calls[0]
    assert params["queue"] == 420 and params["type"] == "ranked"
    assert params["startTime"] == int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())
    assert params["endTime"] == int(datetime(2026, 9, 22, tzinfo=timezone.utc).timestamp())
    assert params["count"] == 100


def test_match_id_listing_pages_past_one_hundred(paths):
    ids = [f"NA1_{n}" for n in range(150)]
    client = FakeClient({"p-gold": ids})

    report = crawl(client, paths, WINDOW, limit=0)

    assert report.stopped == STOP_LIMIT
    assert [p["start"] for _, p in client.calls] == [0, 100]


def test_limit_stops_and_rerun_resumes_without_refetching(paths):
    # Arrange
    client = FakeClient({"p-gold": ["NA1_1", "NA1_2", "NA1_3"]})

    # Act
    first = crawl(client, paths, WINDOW, limit=2)
    second = crawl(client, paths, WINDOW)

    # Assert
    assert first.stopped == STOP_LIMIT and first.fetched == 2
    assert second.fetched == 1
    fetched_matches = [u for u in client.urls("/matches/NA1_") if not u.endswith("/timeline")]
    assert len(fetched_matches) == 3                      # each match fetched exactly once


def test_missing_timeline_is_skipped_and_remembered(paths):
    client = FakeClient({"p-gold": ["NA1_1", "NA1_2"]}, missing={"NA1_1"})

    report = crawl(client, paths, WINDOW)

    assert report.missing == 1 and report.fetched == 1
    assert raw_match_ids(paths.raw) == ("NA1_2",)
    assert "NA1_1" in load_checkpoint(paths.checkpoint).seen


def test_files_already_on_disk_are_not_downloaded_again(paths):
    client = FakeClient({"p-gold": ["NA1_1"]})
    crawl(client, paths, WINDOW)
    paths.checkpoint.unlink()                        # lose the checkpoint, keep the files
    again = FakeClient({"p-gold": ["NA1_1"]})

    report = crawl(again, paths, WINDOW)

    assert report.fetched == 0
    assert again.urls("/matches/NA1_1") == []


def test_auth_error_stops_cleanly_and_keeps_checkpoint(paths):
    client = FakeClient({"p-gold": ["NA1_1"]},
                        fail_on={"/matches/NA1_1": RiotAuthError("renew RIOT_API_KEY")})

    report = crawl(client, paths, WINDOW)

    assert report.stopped == STOP_AUTH
    assert "RIOT_API_KEY" in report.message
    assert load_checkpoint(paths.checkpoint).seed_index == 0


def test_ctrl_c_saves_checkpoint_and_reports_interrupted(paths):
    client = FakeClient({"p-gold": ["NA1_1", "NA1_2"], "p-plat": ["NA1_9"]},
                        fail_on={"/matches/NA1_9": KeyboardInterrupt()})

    report = crawl(client, paths, WINDOW)

    assert report.stopped == STOP_INTERRUPTED
    checkpoint = load_checkpoint(paths.checkpoint)
    assert checkpoint.seed_index == 1
    assert checkpoint.seen == {"NA1_1", "NA1_2"}


def test_collect_seeds_dedupes_and_resumes(tmp_path):
    # Arrange
    paths = DataPaths(tmp_path / "data")
    entry = lambda puuid: {"puuid": puuid, "tier": "GOLD", "rank": "IV", "leaguePoints": 10}
    pages = {("GOLD", "IV", 1): [entry("a"), entry("b")],
             ("GOLD", "IV", 2): [entry("b"), entry("c")]}
    client = FakeClient(league_pages=pages)

    # Act
    first = collect_seeds(client, paths, tiers=("GOLD",), divisions=("IV",), max_pages=5)
    rerun = FakeClient(league_pages=pages)
    second = collect_seeds(rerun, paths, tiers=("GOLD",), divisions=("IV",), max_pages=5)

    # Assert
    assert first.added == 3 and first.pages_fetched == 3      # page 3 empty ends the band
    assert [s.puuid for s in read_seeds(paths.seeds)] == ["a", "b", "c"]
    assert read_seeds(paths.seeds)[0].division == "IV"
    assert second.added == 0 and rerun.calls == []
