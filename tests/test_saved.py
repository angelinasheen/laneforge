"""Saved builds: CRUD, validation, and constraint translation."""
import pytest

from laneforge.queries import saved
from laneforge.queries.errors import ValidationError
from tests.query_fixtures import (
    BANSHEES, DEATHCAP, GameMaker, LUDENS, MERCS, SHADOWFLAME, VOID, ZHONYAS, seed_catalogue,
)
from tests import factories as f

AHRI, ZED, LUX = 103, 238, 99
CORE = [LUDENS, SHADOWFLAME, DEATHCAP]


@pytest.fixture
def setup(conn):
    seed_catalogue(conn, {AHRI: "Ahri", ZED: "Zed", LUX: "Lux"})
    user_id = f.user(conn, "angie")
    games = GameMaker(conn)
    games.games(AHRI, ZED, 2, CORE)
    games.done()
    return conn, user_id


def _create(conn, user_id, **overrides):
    args = dict(name="Anti-Zed", notes="rush Zhonya's", champion_id=AHRI, role="MIDDLE",
                opponent_champion_id=ZED, enemy_champion_ids=[LUX, 900], item_ids=CORE,
                observed=True)
    args.update(overrides)
    return saved.create_build(conn, user_id, **args)


def test_create_and_get_observed_build(setup):
    conn, user_id = setup

    build_id = _create(conn, user_id)
    build = saved.get_build(conn, build_id)

    assert build.name == "Anti-Zed" and build.notes == "rush Zhonya's"
    assert build.user.display_name == "angie"
    assert (build.champion.name, build.opponent.name, build.role) == ("Ahri", "Zed", "MIDDLE")
    assert [c.champion_id for c in build.enemies] == [900, LUX]    # ordered by name
    assert [i.item_id for i in build.items] == CORE
    assert build.is_customized is False
    assert build.stat_totals["ability_power"] == 95 + 110 + 130
    assert build.stat_totals["magic_pen_flat"] == 15
    assert build.stat_totals["gold_cost"] == 2900 + 3200 + 3600


def test_unobserved_sequence_is_saved_as_customized(setup):
    conn, user_id = setup
    build_id = _create(conn, user_id, item_ids=[ZHONYAS, VOID, BANSHEES], observed=True)
    assert saved.get_build(conn, build_id).is_customized is True


def test_observed_false_is_customized(setup):
    conn, user_id = setup
    assert saved.get_build(conn, _create(conn, user_id, observed=False)).is_customized is True


def test_is_observed_sequence(setup):
    conn, _ = setup
    assert saved.is_observed_sequence(conn, AHRI, "MIDDLE", CORE)
    assert not saved.is_observed_sequence(conn, AHRI, "MIDDLE", list(reversed(CORE)))
    assert not saved.is_observed_sequence(conn, AHRI, "TOP", CORE)


def test_list_builds_newest_first(setup):
    conn, user_id = setup
    first = _create(conn, user_id, name="one")
    second = _create(conn, user_id, name="two", enemy_champion_ids=[])
    other = f.user(conn, "someone")
    _create(conn, other, name="theirs")

    builds = saved.list_builds(conn, user_id)

    assert [b.build_id for b in builds] == [second, first]
    assert builds[0].enemies == ()


def test_update_items_reorders_and_marks_customized(setup):
    conn, user_id = setup
    build_id = _create(conn, user_id)

    saved.update_items(conn, build_id, [DEATHCAP, LUDENS, ZHONYAS])

    build = saved.get_build(conn, build_id)
    assert [i.item_id for i in build.items] == [DEATHCAP, LUDENS, ZHONYAS]
    assert build.is_customized is True


def test_rename_and_delete(setup):
    conn, user_id = setup
    build_id = _create(conn, user_id)

    saved.rename_build(conn, build_id, "  New name ", "")
    build = saved.get_build(conn, build_id)
    assert (build.name, build.notes) == ("New name", None)

    saved.delete_build(conn, build_id)
    assert saved.get_build(conn, build_id) is None
    with pytest.raises(ValidationError):
        saved.delete_build(conn, build_id)


def test_writes_to_missing_build_raise(setup):
    conn, _ = setup
    with pytest.raises(ValidationError):
        saved.update_items(conn, 4242, CORE)
    with pytest.raises(ValidationError):
        saved.rename_build(conn, 4242, "x", None)


@pytest.mark.parametrize("overrides, fragment", [
    (dict(item_ids=[LUDENS, SHADOWFLAME]), "exactly 3 items"),
    (dict(item_ids=[LUDENS, LUDENS, DEATHCAP]), "same item twice"),
    (dict(item_ids=[LUDENS, MERCS, DEATHCAP]), "not a legendary"),
    (dict(item_ids=[LUDENS, 1, DEATHCAP]), "Unknown item"),
    (dict(enemy_champion_ids=[900, 901, 902, 903, 904]), "at most 4"),
    (dict(enemy_champion_ids=[ZED]), "The lane opponent is already in the comp"),
    (dict(enemy_champion_ids=[AHRI]), "Your champion can't also be an enemy"),
    (dict(enemy_champion_ids=[900, 900]), "Name each enemy champion only once"),
    (dict(enemy_champion_ids=[555555]), "We don't have that champion"),
    (dict(opponent_champion_id=AHRI, enemy_champion_ids=[]), "can't also be the lane opponent"),
    (dict(name="   "), "Build name"),
    (dict(name="x" * 61), "Build name"),
    (dict(notes="x" * 2001), "Notes"),
    (dict(role="MID"), "Pick a role"),
])
def test_create_validation_errors(setup, overrides, fragment):
    conn, user_id = setup
    with pytest.raises(ValidationError) as err:
        _create(conn, user_id, **overrides)
    assert fragment in err.value.message
    assert conn.execute("SELECT COUNT(*) AS n FROM saved_build").fetchone()["n"] == 0


def test_unknown_user_is_rejected(setup):
    conn, _ = setup
    with pytest.raises(ValidationError, match="Sign in again"):
        _create(conn, 99999)


def test_trigger_violation_becomes_validation_error(setup):
    conn, user_id = setup
    build_id = _create(conn, user_id, enemy_champion_ids=[900, 901, 902, 903])
    # Bypass the application checks to prove the trigger message is translated.
    with pytest.raises(ValidationError, match="at most four"):
        with saved._write(conn):
            conn.execute(saved.SQL_INSERT_ENEMY, {"build_id": build_id, "champion_id": 904})
    assert len(saved.get_build(conn, build_id).enemies) == 4


def test_observed_build_carries_its_matchup_row(setup):
    conn, user_id = setup
    games = GameMaker(conn, prefix="NA1_OBS")
    games.games(AHRI, ZED, 30, CORE, wins=18)
    games.games(AHRI, ZED, 8, [LUDENS, ZHONYAS, VOID])
    games.games(AHRI, LUX, 5, CORE)                  # other matchup: not counted
    games.done()

    build = saved.get_build(conn, _create(conn, user_id))

    row = build.observed
    assert [i.item_id for i in row.items] == CORE
    assert (row.games, row.wins, row.sufficient) == (32, 18, True)
    assert row.pick_rate == pytest.approx(32 / 40)
    assert row.win_rate == pytest.approx(18 / 32)
    assert row.ci_low < row.win_rate < row.ci_high
    assert saved.list_builds(conn, user_id)[0].observed == row


def test_customized_build_has_no_observed_row(setup):
    conn, user_id = setup
    build_id = _create(conn, user_id)
    saved.update_items(conn, build_id, [DEATHCAP, LUDENS, SHADOWFLAME])

    assert saved.get_build(conn, build_id).observed is None
    assert saved.get_build(conn, _create(conn, user_id, name="b", observed=False)).observed is None


def test_observed_sequence_absent_from_this_matchup_has_no_row(setup):
    conn, user_id = setup
    build_id = _create(conn, user_id, opponent_champion_id=LUX, enemy_champion_ids=[])

    build = saved.get_build(conn, build_id)

    assert build.is_customized is False and build.observed is None
