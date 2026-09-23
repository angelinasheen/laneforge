"""Data Dragon catalogue loader, run against the real 16.18.1 JSON."""
from __future__ import annotations

from decimal import Decimal

import pytest

from laneforge.ingest.ddragon import load_catalogue, parse_stats_block


@pytest.fixture
def loaded(conn):
    report = load_catalogue(conn)
    return conn, report


def _item(conn, name):
    return conn.execute("SELECT * FROM item WHERE name = %(n)s ORDER BY item_id LIMIT 1",
                        {"n": name}).fetchone()


def test_loads_every_champion(loaded):
    conn, report = loaded
    count = conn.execute("SELECT COUNT(*) AS n FROM champion").fetchone()["n"]
    assert report.champions_loaded == count
    assert 165 <= count <= 180


def test_item_count_in_expected_range(loaded):
    conn, report = loaded
    count = conn.execute("SELECT COUNT(*) AS n FROM item").fetchone()["n"]
    assert report.items_loaded == count
    assert 200 <= count <= 260
    assert report.items_skipped > 0


def test_duplicate_high_ids_are_collapsed_to_lowest(loaded):
    conn, report = loaded
    assert report.duplicates_collapsed > 0
    high = conn.execute(
        "SELECT COUNT(*) AS n FROM item hi JOIN item lo "
        "ON lo.name = hi.name AND lo.item_id < hi.item_id WHERE hi.item_id > 320000"
    ).fetchone()["n"]
    assert high == 0
    assert _item(conn, "Thornmail")["item_id"] == 3075


def test_ahri_stats(loaded):
    conn, _ = loaded
    ahri = conn.execute("SELECT * FROM champion WHERE ddragon_key = 'Ahri'").fetchone()
    assert ahri["champion_id"] == 103
    assert ahri["name"] == "Ahri"
    assert ahri["base_health"] == 590
    assert ahri["armor_per_level"] == Decimal("4.2")
    assert ahri["base_attack_speed"] == Decimal("0.668")
    assert ahri["attack_speed_per_level_pct"] == Decimal("2.2")


def test_ddragon_key_differs_from_name(loaded):
    conn, _ = loaded
    wukong = conn.execute("SELECT * FROM champion WHERE ddragon_key = 'MonkeyKing'").fetchone()
    assert wukong["name"] == "Wukong"


def test_brutalizer_lethality_parsed(loaded):
    conn, _ = loaded
    brutalizer = _item(conn, "The Brutalizer")
    assert (brutalizer["lethality"], brutalizer["attack_damage"], brutalizer["ability_haste"]) == (5, 25, 10)
    assert brutalizer["is_legendary"] is False


def test_percent_armor_pen_is_a_fraction(loaded):
    conn, _ = loaded
    ldr = _item(conn, "Lord Dominik's Regards")
    assert ldr["armor_pen_pct"] == Decimal("0.350")
    assert ldr["crit_chance_pct"] == Decimal("0.250")
    assert ldr["is_legendary"] is True


def test_magic_pen_flat_vs_percent(loaded):
    conn, _ = loaded
    void_staff = _item(conn, "Void Staff")
    assert void_staff["magic_pen_pct"] == Decimal("0.400")
    assert void_staff["magic_pen_flat"] == 0
    flat = conn.execute("SELECT COUNT(*) AS n FROM item WHERE magic_pen_flat > 0").fetchone()["n"]
    assert flat >= 1


def test_mercurys_treads_is_boots_with_tenacity(loaded):
    conn, _ = loaded
    treads = _item(conn, "Mercury's Treads")
    assert treads["is_boots"] is True
    assert treads["is_legendary"] is False
    assert treads["tenacity_pct"] == Decimal("0.300")
    assert treads["magic_resist"] == 20


def test_grievous_wounds_items(loaded):
    conn, _ = loaded
    n = conn.execute("SELECT COUNT(*) AS n FROM item WHERE applies_grievous_wounds").fetchone()["n"]
    assert n >= 4


def test_no_item_is_both_legendary_and_boots(loaded):
    conn, _ = loaded
    n = conn.execute("SELECT COUNT(*) AS n FROM item WHERE is_legendary AND is_boots").fetchone()["n"]
    assert n == 0


def test_every_legendary_costs_at_least_2000(loaded):
    conn, _ = loaded
    row = conn.execute(
        "SELECT MIN(gold_cost) AS lo, COUNT(*) AS n FROM item WHERE is_legendary").fetchone()
    assert row["n"] >= 60
    assert row["lo"] >= 2000


def test_loader_is_idempotent(loaded):
    conn, first = loaded
    second = load_catalogue(conn)
    assert second == first
    counts = conn.execute(
        "SELECT (SELECT COUNT(*) FROM item) AS items, (SELECT COUNT(*) FROM champion) AS champs"
    ).fetchone()
    assert (counts["items"], counts["champs"]) == (first.items_loaded, first.champions_loaded)


def test_unparsed_labels_are_reported(loaded):
    _, report = loaded
    assert "Adaptive Force" in report.unparsed_stat_labels
    assert "Lethality" not in report.unparsed_stat_labels


def test_parse_stats_block_percent_and_flat():
    description = ("<mainText><stats><attention>12</attention> Magic Penetration<br>"
                   "<attention>30%</attention> Tenacity</stats></mainText>")
    parsed, unparsed = parse_stats_block(description)
    assert parsed == {"magic_pen_flat": 12, "tenacity_pct": Decimal("0.300")}
    assert unparsed == ()


def test_parse_stats_block_without_block():
    assert parse_stats_block("<mainText>no stats</mainText>") == ({}, ())
