from datetime import datetime

from laneforge.queries import catalog
from tests.query_fixtures import (
    BANSHEES, DEATHCAP, GameMaker, LUDENS, MERCS, SHADOWFLAME, seed_catalogue,
)
from tests import factories as f

AHRI, ZED = 103, 238


def test_dataset_summary_on_empty_database(conn):
    summary = catalog.dataset_summary(conn)
    assert summary.patch is None
    assert (summary.matches, summary.participants, summary.tiers) == (0, 0, ())
    assert summary.first_start is None and summary.last_start is None


def test_dataset_summary_counts_and_orders_tiers(conn):
    seed_catalogue(conn, {AHRI: "Ahri", ZED: "Zed"})
    blue, red = [900, 901, AHRI, 902, 903], [904, 905, ZED, 906, 907]
    f.match(conn, "NA1_1", blue, red, seed_tier="PLATINUM", start_time=datetime(2026, 9, 1))
    f.match(conn, "NA1_2", blue, red, seed_tier="GOLD", start_time=datetime(2026, 9, 3))
    conn.commit()

    summary = catalog.dataset_summary(conn)

    assert summary.patch == "16.18"
    assert (summary.matches, summary.participants) == (2, 20)
    assert summary.tiers == ("GOLD", "PLATINUM")
    assert summary.first_start == datetime(2026, 9, 1)
    assert summary.last_start == datetime(2026, 9, 3)


def test_champions_sorted_by_name_and_lookup(conn):
    f.champion(conn, 238, "Zed")
    f.champion(conn, 103, "Ahri", base_armor=21, armor_per_level=4.7)

    champions = catalog.list_champions(conn)

    assert [c.name for c in champions] == ["Ahri", "Zed"]
    ahri = catalog.get_champion(conn, 103)
    assert ahri.ddragon_key == "Ahri" and ahri.armor_per_level == 4.7
    assert isinstance(ahri.armor_per_level, float)
    assert catalog.get_champion(conn, 999) is None


def test_items_with_legendary_filter_and_stat_line(conn):
    seed_catalogue(conn, {})

    everything = catalog.list_items(conn)
    legendary = catalog.list_items(conn, legendary_only=True)

    assert MERCS in {i.item_id for i in everything}
    assert MERCS not in {i.item_id for i in legendary}
    mercs = catalog.get_item(conn, MERCS)
    assert mercs.is_boots and mercs.tenacity_pct == 0.3 and mercs.magic_resist == 20
    assert catalog.get_item(conn, 1) is None


def test_item_usage_counts_core_appearances(conn):
    seed_catalogue(conn, {AHRI: "Ahri", ZED: "Zed"})
    games = GameMaker(conn)
    games.games(AHRI, ZED, 4, [LUDENS, SHADOWFLAME, DEATHCAP], wins=3)
    games.games(AHRI, ZED, 2, [BANSHEES, SHADOWFLAME])
    games.done()

    usage = catalog.item_usage(conn, catalog.get_item(conn, SHADOWFLAME))

    assert usage.completions == 6
    assert (usage.core_games, usage.core_wins) == (4, 3)
    assert usage.top_champions[0].champion.name == "Ahri"
    assert usage.top_champions[0].role == "MIDDLE"
    assert usage.saved_builds == 0
    assert usage.top_champions[0].sufficient is False


def test_item_usage_marks_champions_with_30_games_sufficient(conn):
    seed_catalogue(conn, {AHRI: "Ahri", ZED: "Zed"})
    games = GameMaker(conn)
    games.games(AHRI, ZED, 30, [LUDENS, SHADOWFLAME, DEATHCAP])
    games.done()

    usage = catalog.item_usage(conn, catalog.get_item(conn, SHADOWFLAME))

    assert usage.top_champions[0].games == 30 and usage.top_champions[0].sufficient is True


def test_item_list_hides_free_items_and_collapses_duplicate_names(conn):
    f.item(conn, 3599, "Kalista's Black Spear", is_legendary=False, gold_cost=0)
    f.item(conn, 3600, "Kalista's Black Spear", is_legendary=False, gold_cost=0)
    f.item(conn, 1102, "Gustwalker Hatchling", is_legendary=False, gold_cost=450)
    f.item(conn, 1106, "Gustwalker Hatchling", is_legendary=False, gold_cost=450)
    f.item(conn, 3340, "Stealth Ward", is_legendary=False, gold_cost=0)
    f.item(conn, LUDENS, "Luden's Companion", gold_cost=2900)

    listed = [(i.item_id, i.name) for i in catalog.list_items(conn)]

    assert listed == [(1102, "Gustwalker Hatchling"), (LUDENS, "Luden's Companion")]
    assert catalog.get_item(conn, 3600).name == "Kalista's Black Spear"
    assert catalog.get_item(conn, 1106) is not None
