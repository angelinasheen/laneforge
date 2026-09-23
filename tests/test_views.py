"""Semantics of the materialized views in sql/views.sql."""
from __future__ import annotations

import statistics

import pytest

from laneforge import db
from tests import factories as f

BLUE = [1, 2, 3, 4, 5]          # TOP, JUNGLE, MIDDLE, BOTTOM, UTILITY
RED = [6, 7, 8, 9, 10]
LUDENS, RABADON, SHADOWFLAME, ZHONYA = 101, 102, 103, 104
TREADS, COMPONENT, THORNMAIL, MORTAL = 201, 202, 105, 106
MINUTES = 30                     # factories default duration 1800 s


@pytest.fixture
def world(conn):
    for cid in BLUE + RED + [11]:
        f.champion(conn, cid, f"Champ{cid}")
    f.item(conn, LUDENS, "Ludens", ability_power=95)
    f.item(conn, RABADON, "Rabadon", ability_power=130)
    f.item(conn, SHADOWFLAME, "Shadowflame", ability_power=110)
    f.item(conn, ZHONYA, "Zhonya", ability_power=105, armor=50)
    f.item(conn, THORNMAIL, "Thornmail", armor=75, applies_grievous_wounds=True)
    f.item(conn, MORTAL, "Mortal Reminder", attack_damage=35, applies_grievous_wounds=True)
    f.item(conn, TREADS, "Treads", is_legendary=False, is_boots=True, magic_resist=20,
           tenacity_pct=0.3, gold_cost=1250)
    f.item(conn, COMPONENT, "Component", is_legendary=False, gold_cost=1100, magic_resist=40)
    conn.commit()
    return conn


def _refresh(conn):
    conn.commit()
    db.refresh_views(conn)


def _core(conn, match_id, number):
    return conn.execute(
        "SELECT * FROM participant_core WHERE match_id = %(m)s AND participant_number = %(n)s",
        {"m": match_id, "n": number}).fetchone()


def _events(conn, match_id, number, events):
    """events: (event_number, game_time_ms, item_id)."""
    for event_number, game_time_ms, item_id in events:
        conn.execute(
            "INSERT INTO purchase_event VALUES (%(m)s, %(p)s, %(e)s, %(t)s, %(i)s)",
            {"m": match_id, "p": number, "e": event_number, "t": game_time_ms, "i": item_id})


# --- participant_core: the core build ----------------------------------------

def test_core_is_first_three_legendaries_by_game_time(world):
    f.match(world, "M1", BLUE, RED)
    f.purchases(world, "M1", 3, [TREADS, COMPONENT, RABADON, LUDENS, ZHONYA, SHADOWFLAME])
    _refresh(world)
    row = _core(world, "M1", 3)
    assert (row["item1"], row["item2"], row["item3"]) == (RABADON, LUDENS, ZHONYA)


def test_core_orders_by_game_time_before_event_number(world):
    f.match(world, "M1", BLUE, RED)
    _events(world, "M1", 3, [(1, 900_000, LUDENS), (2, 600_000, RABADON), (3, 700_000, ZHONYA)])
    _refresh(world)
    row = _core(world, "M1", 3)
    assert (row["item1"], row["item2"], row["item3"]) == (RABADON, ZHONYA, LUDENS)


def test_core_breaks_game_time_ties_by_event_number(world):
    f.match(world, "M1", BLUE, RED)
    _events(world, "M1", 3, [(2, 600_000, LUDENS), (1, 600_000, ZHONYA), (3, 600_000, RABADON)])
    _refresh(world)
    row = _core(world, "M1", 3)
    assert (row["item1"], row["item2"], row["item3"]) == (ZHONYA, LUDENS, RABADON)


def test_boots_and_components_excluded_from_core_but_set_flags(world):
    f.match(world, "M1", BLUE, RED)
    f.purchases(world, "M1", 3, [TREADS, COMPONENT, LUDENS])
    _refresh(world)
    row = _core(world, "M1", 3)
    assert row["legendary_completions"] == 1
    assert (row["item1"], row["item2"], row["item3"]) == (LUDENS, None, None)
    assert row["completed_tenacity"] is True          # from boots
    assert row["completed_magic_resist"] is True      # from boots and component
    assert row["completed_armor"] is False
    assert row["completed_grievous_wounds"] is False


def test_class_flags_from_legendaries(world):
    f.match(world, "M1", BLUE, RED)
    f.purchases(world, "M1", 1, [THORNMAIL, ZHONYA])
    _refresh(world)
    row = _core(world, "M1", 1)
    assert row["completed_armor"] is True
    assert row["completed_grievous_wounds"] is True
    assert row["completed_magic_resist"] is False
    assert row["completed_tenacity"] is False


def test_legendary_completions_counts_every_legendary_including_repeats(world):
    f.match(world, "M1", BLUE, RED)
    f.purchases(world, "M1", 3, [TREADS, LUDENS, RABADON, ZHONYA, SHADOWFLAME, ZHONYA])
    _refresh(world)
    assert _core(world, "M1", 3)["legendary_completions"] == 5


def test_participant_without_purchases_has_empty_core(world):
    f.match(world, "M1", BLUE, RED)
    _refresh(world)
    row = _core(world, "M1", 7)
    assert row["legendary_completions"] == 0
    assert (row["item1"], row["item2"], row["item3"]) == (None, None, None)
    assert row["completed_magic_resist"] is False


def test_participant_core_has_one_row_per_participant(world):
    f.match(world, "M1", BLUE, RED)
    f.match(world, "M2", RED, BLUE)
    f.purchases(world, "M1", 3, [LUDENS, RABADON])
    _refresh(world)
    assert world.execute("SELECT COUNT(*) AS n FROM participant_core").fetchone()["n"] == 20


# --- participant_core: outcome and opponent -----------------------------------

@pytest.mark.parametrize("winning_team, blue_won", [(100, True), (200, False)])
def test_won_follows_winning_team(world, winning_team, blue_won):
    f.match(world, "M1", BLUE, RED, winning_team=winning_team)
    _refresh(world)
    assert _core(world, "M1", 3)["won"] is blue_won
    assert _core(world, "M1", 8)["won"] is (not blue_won)


def test_opponent_is_same_role_on_other_team(world):
    f.match(world, "M1", BLUE, RED)
    _refresh(world)
    for number in range(1, 6):
        assert _core(world, "M1", number)["opponent_champion_id"] == RED[number - 1]
        assert _core(world, "M1", number + 5)["opponent_champion_id"] == BLUE[number - 1]


def test_participant_core_carries_match_context(world):
    f.match(world, "M1", BLUE, RED, duration_seconds=2000, seed_tier="PLATINUM")
    _refresh(world)
    row = _core(world, "M1", 1)
    assert (row["champion_id"], row["role"], row["team"]) == (1, "TOP", 100)
    assert (row["duration_seconds"], row["seed_tier"]) == (2000, "PLATINUM")


def test_enemy_columns_come_from_opposite_team(world):
    blue_magic = {n: dict(physical_damage=0, magic_damage=30000, true_damage=0,
                          healing_done=0, cc_seconds=0) for n in range(1, 6)}
    red_physical = {n: dict(physical_damage=30000, magic_damage=0, true_damage=0,
                            healing_done=9000, cc_seconds=60) for n in range(6, 11)}
    f.match(world, "M1", BLUE, RED, measures={**blue_magic, **red_physical})
    _refresh(world)
    blue_row, red_row = _core(world, "M1", 1), _core(world, "M1", 6)
    assert float(blue_row["enemy_physical_share"]) == pytest.approx(1.0)
    assert float(blue_row["enemy_magic_share"]) == pytest.approx(0.0)
    assert float(blue_row["enemy_healing_pm"]) == pytest.approx(5 * 9000 / MINUTES)
    assert float(blue_row["enemy_cc_pm"]) == pytest.approx(5 * 60 / MINUTES)
    assert float(red_row["enemy_magic_share"]) == pytest.approx(1.0)
    assert float(red_row["enemy_healing_pm"]) == pytest.approx(0.0)


# --- champion_role_profile / champion_profile / champion_effective_profile ----

def _effective(conn, champion_id, role):
    return conn.execute(
        "SELECT * FROM champion_effective_profile WHERE champion_id = %(c)s AND role = %(r)s",
        {"c": champion_id, "r": role}).fetchone()


def _play_champion_1(conn, mid_games, top_games):
    """Champion 1 plays MIDDLE (magic 9000) mid_games times and TOP (physical
    6000) top_games times; everyone else keeps the factory defaults."""
    mid = {3: dict(physical_damage=0, magic_damage=9000, true_damage=0,
                   healing_done=600, cc_seconds=15)}
    top = {1: dict(physical_damage=6000, magic_damage=0, true_damage=300,
                   healing_done=0, cc_seconds=3)}
    for n in range(mid_games):
        f.match(conn, f"MID{n}", [2, 11, 1, 4, 5], RED, measures=mid)
    for n in range(top_games):
        f.match(conn, f"TOP{n}", [1, 2, 11, 4, 5], RED, measures=top)


def test_role_profile_is_per_minute_mean(world):
    _play_champion_1(world, mid_games=2, top_games=1)
    _refresh(world)
    row = world.execute("SELECT * FROM champion_role_profile WHERE champion_id = 1 "
                        "AND role = 'MIDDLE'").fetchone()
    assert row["games"] == 2
    assert float(row["magic_pm"]) == pytest.approx(9000 / MINUTES)
    assert float(row["physical_pm"]) == pytest.approx(0.0)


def test_per_minute_normalises_by_each_match_duration(world):
    measures = {3: dict(magic_damage=6000)}
    f.match(world, "A", [2, 11, 1, 4, 5], RED, duration_seconds=1200, measures=measures)
    f.match(world, "B", [2, 11, 1, 4, 5], RED, duration_seconds=2400, measures=measures)
    _refresh(world)
    row = world.execute("SELECT magic_pm FROM champion_profile WHERE champion_id = 1").fetchone()
    assert float(row["magic_pm"]) == pytest.approx((300 + 150) / 2)


def test_effective_profile_uses_champion_mean_below_50_role_games(world):
    _play_champion_1(world, mid_games=49, top_games=5)
    _refresh(world)
    row = _effective(world, 1, "MIDDLE")
    assert row["source"] == "champion"
    assert (row["role_games"], row["champion_games"]) == (49, 54)
    assert float(row["magic_pm"]) == pytest.approx(49 * 9000 / MINUTES / 54)
    assert float(row["physical_pm"]) == pytest.approx(5 * 6000 / MINUTES / 54)


def test_effective_profile_uses_role_profile_at_50_role_games(world):
    _play_champion_1(world, mid_games=50, top_games=5)
    _refresh(world)
    row = _effective(world, 1, "MIDDLE")
    assert row["source"] == "role"
    assert row["role_games"] == 50
    assert float(row["magic_pm"]) == pytest.approx(9000 / MINUTES)
    assert float(row["physical_pm"]) == pytest.approx(0.0)
    assert float(row["healing_pm"]) == pytest.approx(600 / MINUTES)
    top = _effective(world, 1, "TOP")
    assert top["source"] == "champion"
    assert float(top["magic_pm"]) == pytest.approx(50 * 9000 / MINUTES / 55)


def test_effective_profile_has_every_champion_and_role(world):
    f.match(world, "M1", BLUE, RED)
    _refresh(world)
    n = world.execute("SELECT COUNT(*) AS n FROM champion_effective_profile").fetchone()["n"]
    assert n == 11 * 5


def test_unseen_champion_has_zero_games_and_null_profile(world):
    f.match(world, "M1", BLUE, RED)
    _refresh(world)
    row = _effective(world, 11, "TOP")
    assert (row["role_games"], row["champion_games"], row["source"]) == (0, 0, "champion")
    assert row["magic_pm"] is None and row["healing_pm"] is None


def test_null_profile_contributes_nothing_to_a_sum(world):
    f.match(world, "M1", BLUE, RED)
    _refresh(world)
    row = world.execute(
        "SELECT SUM(magic_pm) AS s, COUNT(magic_pm) AS n FROM champion_effective_profile "
        "WHERE role = 'MIDDLE' AND champion_id IN (3, 11)").fetchone()
    only_three = _effective(world, 3, "MIDDLE")["magic_pm"]
    assert row["n"] == 1
    assert row["s"] == only_three


# --- team_comp_profile ---------------------------------------------------------

def test_team_comp_shares_sum_to_one_with_true_share(world):
    measures = {n: dict(physical_damage=1000 * n, magic_damage=500 * (11 - n),
                        true_damage=100 * n, healing_done=200 * n, cc_seconds=n)
                for n in range(1, 11)}
    f.match(world, "M1", BLUE, RED, measures=measures)
    _refresh(world)
    row = world.execute("SELECT * FROM team_comp_profile WHERE match_id = 'M1' AND team = 100"
                        ).fetchone()
    physical = sum(1000 * n for n in range(1, 6))
    magic = sum(500 * (11 - n) for n in range(1, 6))
    true = sum(100 * n for n in range(1, 6))
    total = physical + magic + true
    assert float(row["magic_share"]) == pytest.approx(magic / total)
    assert float(row["physical_share"]) == pytest.approx(physical / total)
    assert float(row["magic_share"]) + float(row["physical_share"]) + true / total == pytest.approx(1.0)
    assert float(row["healing_pm"]) == pytest.approx(sum(200 * n for n in range(1, 6)) / MINUTES)
    assert float(row["cc_pm"]) == pytest.approx(15 / MINUTES)


def test_team_comp_uses_expected_profiles_not_realised_damage(world):
    """Two games for the same ten champions: each team's comp profile is the
    same in both, the mean of what its champions did, not that game's numbers."""
    f.match(world, "M1", BLUE, RED, measures={3: dict(magic_damage=20000)})
    f.match(world, "M2", BLUE, RED, measures={3: dict(magic_damage=0)})
    _refresh(world)
    rows = world.execute("SELECT match_id, magic_share FROM team_comp_profile WHERE team = 100 "
                         "ORDER BY match_id").fetchall()
    assert rows[0]["magic_share"] == rows[1]["magic_share"]


def test_team_comp_profile_has_two_rows_per_match(world):
    f.match(world, "M1", BLUE, RED)
    f.match(world, "M2", RED, BLUE)
    _refresh(world)
    assert world.execute("SELECT COUNT(*) AS n FROM team_comp_profile").fetchone()["n"] == 4


# --- comp_thresholds -------------------------------------------------------------

def test_comp_thresholds_are_75th_percentiles(world):
    champions = BLUE + RED
    for n in range(4):
        blue = champions[n:] + champions[:n]
        measures = {k: dict(healing_done=1000 * (n + 1) * k, cc_seconds=5 * k * (n + 1))
                    for k in range(1, 11)}
        f.match(world, f"M{n}", blue[:5], blue[5:], measures=measures)
    _refresh(world)
    comps = world.execute("SELECT healing_pm, cc_pm FROM team_comp_profile").fetchall()
    thresholds = world.execute("SELECT * FROM comp_thresholds").fetchone()
    healing = [float(r["healing_pm"]) for r in comps]
    cc = [float(r["cc_pm"]) for r in comps]
    assert thresholds["comps"] == 8
    assert thresholds["healing_pm_p75"] == pytest.approx(
        statistics.quantiles(healing, n=4, method="inclusive")[2])
    assert thresholds["cc_pm_p75"] == pytest.approx(
        statistics.quantiles(cc, n=4, method="inclusive")[2])


def test_comp_thresholds_on_empty_data(world):
    _refresh(world)
    row = world.execute("SELECT * FROM comp_thresholds").fetchone()
    assert row["comps"] == 0
    assert row["healing_pm_p75"] is None


# --- refresh ---------------------------------------------------------------------

VIEWS = ("champion_role_profile", "champion_profile", "champion_effective_profile",
         "team_comp_profile", "comp_thresholds", "participant_core")


def _snapshot(conn):
    return {view: conn.execute(f"SELECT * FROM {view} ORDER BY 1, 2").fetchall()
            for view in VIEWS}


def test_refresh_views_is_idempotent(world):
    f.match(world, "M1", BLUE, RED)
    f.match(world, "M2", RED, BLUE, winning_team=200)
    f.purchases(world, "M1", 3, [TREADS, LUDENS, RABADON, ZHONYA])
    _refresh(world)
    first = _snapshot(world)
    db.refresh_views(world)
    assert _snapshot(world) == first


def test_refresh_picks_up_new_matches(world):
    f.match(world, "M1", BLUE, RED)
    _refresh(world)
    f.match(world, "M2", RED, BLUE)
    _refresh(world)
    assert world.execute("SELECT COUNT(*) AS n FROM participant_core").fetchone()["n"] == 20
