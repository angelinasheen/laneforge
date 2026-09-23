from datetime import datetime

import pytest

from laneforge.queries import browse
from laneforge.queries.errors import ValidationError
from tests.query_fixtures import DEATHCAP, GameMaker, LUDENS, SHADOWFLAME, seed_catalogue
from tests import factories as f

AHRI, ZED, LUX = 103, 238, 99


@pytest.fixture
def data(conn):
    seed_catalogue(conn, {AHRI: "Ahri", ZED: "Zed", LUX: "Lux"})
    games = GameMaker(conn)
    games.games(AHRI, ZED, 3, [LUDENS, SHADOWFLAME, DEATHCAP], wins=2)
    games.games(AHRI, LUX, 1, [LUDENS])
    games.games(AHRI, ZED, 2, [], role="TOP")
    f.match(conn, "NA1_900", [900, 901, 902, 903, 904], [905, 906, 907, 908, 909],
            seed_tier="PLATINUM", start_time=datetime(2026, 9, 20))
    games.done()
    return conn


def test_recent_matches_newest_first_with_teams_in_role_order(data):
    matches = browse.recent_matches(data, limit=3)
    assert len(matches) == 3
    assert matches[0].match_id == "NA1_900"
    assert [c.champion_id for c in matches[0].blue] == [900, 901, 902, 903, 904]
    assert len(matches[1].red) == 5


def test_recent_matches_tier_filter(data):
    assert [m.match_id for m in browse.recent_matches(data, tier="PLATINUM")] == ["NA1_900"]
    assert len(browse.recent_matches(data, tier="GOLD")) == 6


def test_get_match_has_ten_participants_and_ordered_purchases(data):
    match = browse.get_match(data, "NA1_1")
    assert len(match.participants) == 10
    ahri = match.participants[2]
    assert ahri.champion.name == "Ahri" and ahri.won is True and ahri.team == 100
    assert [p.item.item_id for p in ahri.purchases] == [LUDENS, SHADOWFLAME, DEATHCAP]
    assert ahri.purchases[0].game_time_ms == 600_000
    assert match.participants[7].won is False


def test_get_match_unknown_is_none(data):
    assert browse.get_match(data, "NA1_nope") is None


def test_champion_overview_roles_profiles_and_matchups(data):
    overview = browse.champion_overview(data, AHRI)
    assert (overview.games, overview.wins) == (6, 2)
    assert [r.role for r in overview.by_role] == ["TOP", "MIDDLE"]
    mid = overview.by_role[1]
    assert (mid.games, mid.wins, mid.profile_source) == (4, 2, "champion")
    assert mid.magic_pm == pytest.approx(10000 * 60 / 1800)
    assert [(m.opponent.name, m.role, m.games) for m in overview.top_matchups] == [
        ("Zed", "MIDDLE", 3), ("Zed", "TOP", 2), ("Lux", "MIDDLE", 1)]


def test_champion_overview_for_unplayed_champion(data):
    f.champion(data, 1, "Annie")
    overview = browse.champion_overview(data, 1)
    assert (overview.games, overview.by_role, overview.top_matchups) == (0, (), ())
    with pytest.raises(ValidationError):
        browse.champion_overview(data, 31337)
