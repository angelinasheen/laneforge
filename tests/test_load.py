"""Loading recorded Riot fixtures into the real test database."""
from __future__ import annotations

import gzip
import logging
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from laneforge.ingest.load import UNKNOWN_TIER, load_raw_dir
from tests import factories

FIXTURES = Path(__file__).parent / "fixtures" / "riot"
PATCH = "16.18"
MATCH_ID = "NA1_5100000001"
REMAKE_ID = "NA1_5100000002"
WRONG_PATCH_ID = "NA1_5100000003"

CHAMPIONS = {86: "Garen", 64: "Lee Sin", 103: "Ahri", 222: "Jinx", 412: "Thresh",
             122: "Darius", 254: "Vi", 238: "Zed", 51: "Caitlyn", 99: "Lux"}
DORANS_BLADE, INFINITY_EDGE, KRAKEN, BLOODTHIRSTER = 1055, 3031, 6672, 3072
MANAMUNE, MURAMANA, BLACK_CLEAVER, GREAVES = 3004, 3042, 3071, 3006
WORLD_ATLAS = 3865   # the fixture timeline has a participantId 0 purchase of it, as real ones do
# What completion.py derives from item.json for these ids: Doran's Blade has no
# `into` so it counts; Greaves (into 3172) and Muramana (specialRecipe) do not.
COMPLETED_IDS = frozenset({DORANS_BLADE, INFINITY_EDGE, KRAKEN, BLOODTHIRSTER, MANAMUNE,
                           BLACK_CLEAVER, WORLD_ATLAS})


def gz(src: Path, dest: Path) -> None:
    dest.write_bytes(gzip.compress(src.read_bytes()))


@pytest.fixture
def catalogue(conn):
    for champion_id, name in CHAMPIONS.items():
        factories.champion(conn, champion_id, name)
    for item_id, name, legendary, boots in [
        (DORANS_BLADE, "Doran's Blade", False, False), (INFINITY_EDGE, "Infinity Edge", True, False),
        (KRAKEN, "Kraken Slayer", True, False), (BLOODTHIRSTER, "Bloodthirster", True, False),
        (MANAMUNE, "Manamune", True, False), (MURAMANA, "Muramana", True, False),
        (BLACK_CLEAVER, "Black Cleaver", True, False), (GREAVES, "Berserker's Greaves", False, True),
        (WORLD_ATLAS, "World Atlas", False, False),
    ]:
        factories.item(conn, item_id, name, is_legendary=legendary, is_boots=boots)
    conn.commit()
    return conn


@pytest.fixture
def raw_dir(tmp_path) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    for match_id, name in [(MATCH_ID, "match.json"), (REMAKE_ID, "remake.json"),
                           (WRONG_PATCH_ID, "wrong_patch.json")]:
        gz(FIXTURES / name, raw / f"{match_id}.json.gz")
        gz(FIXTURES / "timeline.json", raw / f"{match_id}.timeline.json.gz")
    shutil.copy(FIXTURES / "match.meta.json", raw / f"{MATCH_ID}.meta.json")
    return raw


def count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


def items_of(conn, participant_number: int) -> list[tuple[int, int, int]]:
    rows = conn.execute(
        "SELECT event_number, game_time_ms, item_id FROM purchase_event "
        "WHERE match_id = %(m)s AND participant_number = %(p)s ORDER BY event_number",
        {"m": MATCH_ID, "p": participant_number},
    ).fetchall()
    return [(r["event_number"], r["game_time_ms"], r["item_id"]) for r in rows]


def test_loads_one_match_with_ten_participants_and_replayed_purchases(catalogue, raw_dir):
    # Act
    report = load_raw_dir(catalogue, raw_dir, PATCH, COMPLETED_IDS)

    # Assert
    assert report.loaded == 1
    assert count(catalogue, "match") == 1
    assert count(catalogue, "participant") == 10
    assert count(catalogue, "purchase_event") == 9  # Jinx 4, Caitlyn 3, Garen 1, Darius 1
    # Jinx: IE bought/undone/rebought counts once at the rebuy; the sold Doran's
    # Blade still counts; Greaves (boots, has `into`) and components are excluded.
    assert items_of(catalogue, 4) == [
        (1, 15_000, DORANS_BLADE), (2, 960_000, INFINITY_EDGE),
        (3, 1_300_000, KRAKEN), (4, 1_700_000, BLOODTHIRSTER),
    ]
    # Caitlyn: the Muramana transformation is dropped; Manamune's completion stays.
    assert [i for _, _, i in items_of(catalogue, 9)] == [DORANS_BLADE, MANAMUNE, INFINITY_EDGE]


def test_match_row_carries_time_duration_winner_and_seed_tier(catalogue, raw_dir):
    load_raw_dir(catalogue, raw_dir, PATCH, COMPLETED_IDS)

    row = catalogue.execute("SELECT * FROM match WHERE match_id = %(m)s", {"m": MATCH_ID}).fetchone()

    assert row["start_time"] == datetime(2026, 9, 17, 12, 0, 0)
    assert row["duration_seconds"] == 1850
    assert row["winning_team"] == 200
    assert row["seed_tier"] == "GOLD"
    assert row["game_version"] == "16.18.712.1234"


def test_participant_rows_carry_role_team_and_measures(catalogue, raw_dir):
    load_raw_dir(catalogue, raw_dir, PATCH, COMPLETED_IDS)

    jinx = catalogue.execute(
        "SELECT * FROM participant WHERE match_id = %(m)s AND champion_id = 222", {"m": MATCH_ID},
    ).fetchone()

    assert (jinx["participant_number"], jinx["team"], jinx["role"]) == (4, 100, "BOTTOM")
    assert jinx["magic_damage"] == 2000 + 900 * 4
    assert jinx["cc_seconds"] == 22


def test_remake_and_wrong_patch_are_skipped_with_reasons(catalogue, raw_dir, caplog):
    caplog.set_level(logging.INFO, logger="laneforge.ingest.load")

    report = load_raw_dir(catalogue, raw_dir, PATCH, COMPLETED_IDS)

    assert dict(report.skipped_by_reason) == {"early_surrender": 1, "wrong_patch": 1}
    assert f"skip {REMAKE_ID}: early_surrender" in caplog.text
    assert f"skip {WRONG_PATCH_ID}: wrong_patch" in caplog.text


def test_loading_twice_is_idempotent(catalogue, raw_dir):
    load_raw_dir(catalogue, raw_dir, PATCH, COMPLETED_IDS)

    second = load_raw_dir(catalogue, raw_dir, PATCH, COMPLETED_IDS)

    assert second.loaded == 0 and second.already_present == 1
    assert count(catalogue, "match") == 1
    assert count(catalogue, "purchase_event") == 9  # Jinx 4, Caitlyn 3, Garen 1, Darius 1


def test_missing_meta_file_falls_back_to_unknown_tier(catalogue, raw_dir):
    (raw_dir / f"{MATCH_ID}.meta.json").unlink()

    load_raw_dir(catalogue, raw_dir, PATCH, COMPLETED_IDS)

    tier = catalogue.execute("SELECT seed_tier FROM match").fetchone()["seed_tier"]
    assert tier == UNKNOWN_TIER


def test_unknown_champion_skips_the_whole_match(catalogue, raw_dir):
    catalogue.execute("DELETE FROM champion WHERE champion_id = 99")
    catalogue.commit()

    report = load_raw_dir(catalogue, raw_dir, PATCH, COMPLETED_IDS)

    assert report.loaded == 0
    assert report.skipped_by_reason["unknown_champion"] == 1
    assert count(catalogue, "participant") == 0


def test_items_missing_from_item_table_are_not_stored(catalogue, raw_dir):
    catalogue.execute("DELETE FROM item WHERE item_id = %(i)s", {"i": BLACK_CLEAVER})
    catalogue.commit()

    load_raw_dir(catalogue, raw_dir, PATCH, COMPLETED_IDS)

    assert count(catalogue, "purchase_event") == 7
    assert items_of(catalogue, 1) == []


def test_empty_catalogue_is_an_explicit_error(conn, raw_dir):
    with pytest.raises(RuntimeError, match="ddragon"):
        load_raw_dir(conn, raw_dir, PATCH, COMPLETED_IDS)
