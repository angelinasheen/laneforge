"""The DDL and triggers enforce draft 4's "Constraints not expressible in E/R"
(numbers in test names). Constraints 7 and 11 and the counts in 1 and 4 are
application/load-time checks and are tested by the modules that own them."""
from __future__ import annotations

import pytest
from psycopg import errors

from tests import factories as f

BLUE = [1, 2, 3, 4, 5]
RED = [6, 7, 8, 9, 10]
LEGENDARIES = (101, 102, 103)
BOOTS = 201
COMPONENT = 202


@pytest.fixture
def world(conn):
    for cid in BLUE + RED + [11, 12]:
        f.champion(conn, cid, f"Champ{cid}")
    for iid in LEGENDARIES:
        f.item(conn, iid, f"Legendary{iid}", ability_power=80)
    f.item(conn, BOOTS, "Boots", is_legendary=False, is_boots=True, gold_cost=1100)
    f.item(conn, COMPONENT, "Component", is_legendary=False, gold_cost=1200)
    conn.commit()
    return conn


def _build(conn, champion_id=1, opponent_id=6, user_name="tester") -> int:
    user_id = f.user(conn, user_name)
    return conn.execute(
        "INSERT INTO saved_build (user_id, champion_id, opponent_champion_id, name, role) "
        "VALUES (%(u)s, %(c)s, %(o)s, 'my build', 'MIDDLE') RETURNING build_id",
        {"u": user_id, "c": champion_id, "o": opponent_id},
    ).fetchone()["build_id"]


def _add_item(conn, build_id, item_id, position):
    conn.execute("INSERT INTO build_item (build_id, item_id, position) "
                 "VALUES (%(b)s, %(i)s, %(p)s)", {"b": build_id, "i": item_id, "p": position})


def _add_enemy(conn, build_id, champion_id):
    conn.execute("INSERT INTO saved_build_enemy (build_id, champion_id) VALUES (%(b)s, %(c)s)",
                 {"b": build_id, "c": champion_id})


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


# --- constraint 1: positions 1..3, each used once; items distinct ------------

def test_c1_position_outside_1_to_3_rejected(world):
    build_id = _build(world)
    with pytest.raises(errors.CheckViolation):
        _add_item(world, build_id, 101, 4)


def test_c1_same_position_twice_rejected(world):
    build_id = _build(world)
    _add_item(world, build_id, 101, 1)
    with pytest.raises(errors.UniqueViolation):
        _add_item(world, build_id, 102, 1)


def test_c1_same_item_twice_rejected(world):
    build_id = _build(world)
    _add_item(world, build_id, 101, 1)
    with pytest.raises(errors.UniqueViolation):
        _add_item(world, build_id, 101, 2)


def test_c1_valid_three_item_build_accepted(world):
    build_id = _build(world)
    for position, item_id in enumerate(LEGENDARIES, start=1):
        _add_item(world, build_id, item_id, position)
    assert _count(world, "build_item") == 3


# --- constraint 2: champion differs from lane opponent -----------------------

def test_c2_champion_equal_to_opponent_rejected(world):
    with pytest.raises(errors.CheckViolation):
        _build(world, champion_id=1, opponent_id=1)


# --- constraint 3: at most four comp champions, none equal to champion/opponent

def test_c3_duplicate_comp_champion_rejected(world):
    build_id = _build(world)
    _add_enemy(world, build_id, 7)
    with pytest.raises(errors.UniqueViolation):
        _add_enemy(world, build_id, 7)


def test_c3_four_comp_champions_accepted(world):
    build_id = _build(world)
    for cid in (7, 8, 9, 10):
        _add_enemy(world, build_id, cid)
    assert _count(world, "saved_build_enemy") == 4


def test_c3_fifth_comp_champion_rejected(world):
    build_id = _build(world)
    for cid in (7, 8, 9, 10):
        _add_enemy(world, build_id, cid)
    with pytest.raises(errors.CheckViolation):
        _add_enemy(world, build_id, 11)


def test_c3_comp_champion_equal_to_build_champion_rejected(world):
    build_id = _build(world, champion_id=1, opponent_id=6)
    with pytest.raises(errors.CheckViolation):
        _add_enemy(world, build_id, 1)


def test_c3_comp_champion_equal_to_opponent_rejected(world):
    build_id = _build(world, champion_id=1, opponent_id=6)
    with pytest.raises(errors.CheckViolation):
        _add_enemy(world, build_id, 6)


def test_c3_updating_build_champion_to_listed_comp_champion_rejected(world):
    build_id = _build(world, champion_id=1, opponent_id=6)
    _add_enemy(world, build_id, 7)
    with pytest.raises(errors.CheckViolation):
        world.execute("UPDATE saved_build SET champion_id = 7 WHERE build_id = %(b)s",
                      {"b": build_id})


def test_c3_updating_build_opponent_to_listed_comp_champion_rejected(world):
    build_id = _build(world, champion_id=1, opponent_id=6)
    _add_enemy(world, build_id, 7)
    with pytest.raises(errors.CheckViolation):
        world.execute("UPDATE saved_build SET opponent_champion_id = 7 WHERE build_id = %(b)s",
                      {"b": build_id})


def test_c3_updating_build_champion_to_unlisted_champion_accepted(world):
    build_id = _build(world, champion_id=1, opponent_id=6)
    _add_enemy(world, build_id, 7)
    world.execute("UPDATE saved_build SET champion_id = 2 WHERE build_id = %(b)s",
                  {"b": build_id})
    assert world.execute("SELECT champion_id FROM saved_build").fetchone()["champion_id"] == 2


# --- constraint 4: one participant per (match, team, role); role domain ------

def test_c4_two_participants_in_same_team_role_rejected(world):
    f.match(world, "NA1_1", BLUE, RED)
    with pytest.raises(errors.UniqueViolation):
        world.execute(
            "INSERT INTO participant VALUES ('NA1_1', 10, 100, 'TOP', 11, 0, 0, 0, 0, 0)")


def test_c4_unknown_role_rejected(world):
    world.execute("INSERT INTO match VALUES ('NA1_1', '16.18.1', now(), 1800, 100, 'GOLD')")
    with pytest.raises(errors.CheckViolation):
        world.execute(
            "INSERT INTO participant VALUES ('NA1_1', 1, 100, 'ADC', 1, 0, 0, 0, 0, 0)")


def test_c4_participant_number_and_team_domains(world):
    world.execute("INSERT INTO match VALUES ('NA1_1', '16.18.1', now(), 1800, 100, 'GOLD')")
    world.commit()
    with pytest.raises(errors.CheckViolation):
        world.execute("INSERT INTO participant VALUES ('NA1_1', 11, 100, 'TOP', 1, 0, 0, 0, 0, 0)")
    world.rollback()
    with pytest.raises(errors.CheckViolation):
        world.execute("INSERT INTO participant VALUES ('NA1_1', 1, 300, 'TOP', 1, 0, 0, 0, 0, 0)")


def test_c4_winning_team_domain(world):
    with pytest.raises(errors.CheckViolation):
        world.execute("INSERT INTO match VALUES ('NA1_1', '16.18.1', now(), 1800, 150, 'GOLD')")


# --- constraint 5: ten distinct champions per match ---------------------------

def test_c5_same_champion_twice_in_match_rejected(world):
    with pytest.raises(errors.UniqueViolation):
        f.match(world, "NA1_1", BLUE, [6, 7, 8, 9, 1])


# --- constraint 6: purchase time within the match -----------------------------

def test_c6_purchase_after_match_end_rejected(world):
    f.match(world, "NA1_1", BLUE, RED, duration_seconds=1800)
    with pytest.raises(errors.CheckViolation):
        f.purchases(world, "NA1_1", 1, [101], start_ms=1_800_001)


def test_c6_purchase_at_exact_match_end_accepted(world):
    f.match(world, "NA1_1", BLUE, RED, duration_seconds=1800)
    f.purchases(world, "NA1_1", 1, [101], start_ms=1_800_000)
    assert _count(world, "purchase_event") == 1


def test_c6_update_moving_purchase_past_end_rejected(world):
    f.match(world, "NA1_1", BLUE, RED, duration_seconds=1800)
    f.purchases(world, "NA1_1", 1, [101])
    with pytest.raises(errors.CheckViolation):
        world.execute("UPDATE purchase_event SET game_time_ms = 9999999")


def test_c6_negative_game_time_rejected(world):
    f.match(world, "NA1_1", BLUE, RED)
    with pytest.raises(errors.CheckViolation):
        f.purchases(world, "NA1_1", 1, [101], start_ms=-1)


def test_purchase_for_unknown_participant_rejected(world):
    world.execute("INSERT INTO match VALUES ('NA1_1', '16.18.1', now(), 1800, 100, 'GOLD')")
    with pytest.raises(errors.ForeignKeyViolation):
        f.purchases(world, "NA1_1", 1, [101])


def test_purchase_of_unknown_item_rejected(world):
    f.match(world, "NA1_1", BLUE, RED)
    with pytest.raises(errors.ForeignKeyViolation):
        f.purchases(world, "NA1_1", 1, [999999])


# --- constraint 8: builds contain only legendary, non-boots items -------------

def test_c8_non_legendary_item_in_build_rejected(world):
    build_id = _build(world)
    with pytest.raises(errors.CheckViolation):
        _add_item(world, build_id, COMPONENT, 1)


def test_c8_boots_in_build_rejected(world):
    build_id = _build(world)
    with pytest.raises(errors.CheckViolation):
        _add_item(world, build_id, BOOTS, 1)


def test_c8_updating_build_item_to_boots_rejected(world):
    build_id = _build(world)
    _add_item(world, build_id, 101, 1)
    with pytest.raises(errors.CheckViolation):
        world.execute("UPDATE build_item SET item_id = %(i)s", {"i": BOOTS})


# --- constraint 9: never both legendary and boots -----------------------------

def test_c9_legendary_boots_rejected(world):
    with pytest.raises(errors.CheckViolation):
        f.item(world, 300, "Legendary Boots", is_legendary=True, is_boots=True)


# --- constraint 10: non-negative measures and stats ---------------------------

@pytest.mark.parametrize("measure", ["physical_damage", "magic_damage", "true_damage",
                                     "healing_done", "cc_seconds"])
def test_c10_negative_participant_measure_rejected(world, measure):
    with pytest.raises(errors.CheckViolation):
        f.match(world, "NA1_1", BLUE, RED, measures={1: {measure: -1}})


@pytest.mark.parametrize("stat", ["attack_damage", "armor", "magic_resist", "lethality",
                                  "armor_pen_pct", "tenacity_pct", "gold_cost"])
def test_c10_negative_item_stat_rejected(world, stat):
    with pytest.raises(errors.CheckViolation):
        f.item(world, 300, "Bad", **{stat: -1})


@pytest.mark.parametrize("stat", ["base_armor", "armor_per_level", "magic_resist_per_level"])
def test_c10_negative_champion_stat_rejected(world, stat):
    with pytest.raises(errors.CheckViolation):
        f.champion(world, 500, "Bad", **{stat: -1})


def test_c10_zero_base_health_rejected(world):
    with pytest.raises(errors.CheckViolation):
        f.champion(world, 500, "Bad", base_health=0)


def test_match_duration_must_be_positive(world):
    with pytest.raises(errors.CheckViolation):
        world.execute("INSERT INTO match VALUES ('NA1_1', '16.18.1', now(), 0, 100, 'GOLD')")


# --- other column rules -------------------------------------------------------

def test_display_name_shorter_than_two_chars_rejected(world):
    with pytest.raises(errors.CheckViolation):
        f.user(world, " a ")


def test_duplicate_display_name_rejected(world):
    f.user(world, "angie")
    with pytest.raises(errors.UniqueViolation):
        f.user(world, "angie")


def test_blank_build_name_rejected(world):
    user_id = f.user(world, "tester")
    with pytest.raises(errors.CheckViolation):
        world.execute(
            "INSERT INTO saved_build (user_id, champion_id, opponent_champion_id, name, role) "
            "VALUES (%(u)s, 1, 6, '   ', 'MIDDLE')", {"u": user_id})


def test_build_for_unknown_champion_rejected(world):
    with pytest.raises(errors.ForeignKeyViolation):
        _build(world, champion_id=9999)


# --- cascades -----------------------------------------------------------------

def test_deleting_match_cascades_to_participants_and_purchases(world):
    f.match(world, "NA1_1", BLUE, RED)
    f.match(world, "NA1_2", BLUE, RED)
    f.purchases(world, "NA1_1", 1, [101, 102])
    f.purchases(world, "NA1_2", 1, [101])
    world.execute("DELETE FROM match WHERE match_id = 'NA1_1'")
    assert _count(world, "participant") == 10
    assert _count(world, "purchase_event") == 1


def test_deleting_participant_cascades_to_purchases(world):
    f.match(world, "NA1_1", BLUE, RED)
    f.purchases(world, "NA1_1", 1, [101, 102])
    world.execute("DELETE FROM participant WHERE participant_number = 1")
    assert _count(world, "purchase_event") == 0


def test_deleting_user_cascades_to_builds_items_and_enemies(world):
    build_id = _build(world)
    for position, item_id in enumerate(LEGENDARIES, start=1):
        _add_item(world, build_id, item_id, position)
    _add_enemy(world, build_id, 7)
    world.execute("DELETE FROM user_profile")
    assert (_count(world, "saved_build"), _count(world, "build_item"),
            _count(world, "saved_build_enemy")) == (0, 0, 0)


def test_deleting_champion_used_by_participant_is_restricted(world):
    f.match(world, "NA1_1", BLUE, RED)
    with pytest.raises(errors.ForeignKeyViolation):
        world.execute("DELETE FROM champion WHERE champion_id = 1")


def test_deleting_item_in_a_build_is_restricted(world):
    build_id = _build(world)
    _add_item(world, build_id, 101, 1)
    with pytest.raises(errors.ForeignKeyViolation):
        world.execute("DELETE FROM item WHERE item_id = 101")
