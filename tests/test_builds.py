"""The four-level core-build ladder (draft 4 "Core builds")."""
import pytest

from laneforge.queries.builds import core_builds
from laneforge.queries.errors import ValidationError
from tests.query_fixtures import (
    BANSHEES, CRYPTBLOOM, DEATHCAP, GameMaker, LUDENS, MORELLO, SHADOWFLAME, VOID, ZHONYAS,
    seed_catalogue,
)

AHRI, ZED, SYNDRA, LUX, ORIANNA = 103, 238, 134, 99, 61
SEQ_A = [LUDENS, SHADOWFLAME, DEATHCAP]
SEQ_B = [LUDENS, ZHONYAS, VOID]


@pytest.fixture
def games(conn):
    seed_catalogue(conn, {AHRI: "Ahri", ZED: "Zed", SYNDRA: "Syndra", LUX: "Lux",
                          ORIANNA: "Orianna"})
    return GameMaker(conn)


def _ids(row):
    return [i.item_id for i in row.items]


def test_level_one_answers_when_top_matchup_sequence_has_30_games(games):
    games.games(AHRI, ZED, 35, SEQ_A, wins=20)
    games.done()

    answer = core_builds(games.conn, AHRI, "MIDDLE", ZED)

    assert (answer.level, answer.scope, answer.granularity) == (1, "matchup", "sequence")
    assert answer.fell_back is False and answer.fallback_note is None
    assert answer.sample_size == 35
    assert answer.label == "vs Zed, mid: 35 games"
    top = answer.rows[0]
    assert _ids(top) == SEQ_A
    assert (top.games, top.wins, top.pick_rate, top.sufficient) == (35, 20, 1.0, True)
    assert top.win_rate == pytest.approx(20 / 35)
    assert top.ci_low < top.win_rate < top.ci_high


def test_level_two_per_item_when_no_sequence_reaches_30(games):
    games.games(AHRI, ZED, 20, SEQ_A, wins=10)
    games.games(AHRI, ZED, 15, SEQ_B, wins=5)
    games.done()

    answer = core_builds(games.conn, AHRI, "MIDDLE", ZED)

    assert (answer.level, answer.scope, answer.granularity) == (2, "matchup", "item")
    assert answer.fell_back is True and "Zed" in answer.fallback_note
    assert answer.sample_size == 35
    top = answer.rows[0]
    assert _ids(top) == [LUDENS]
    assert (top.games, top.wins, top.pick_rate) == (35, 15, 1.0)
    second = answer.rows[1]
    assert _ids(second) == [SHADOWFLAME] or _ids(second) == [DEATHCAP]
    assert second.games == 20 and second.sufficient is False
    assert second.pick_rate == pytest.approx(20 / 35)


def test_level_three_champion_sequence_when_matchup_is_thin(games):
    games.games(AHRI, ZED, 10, SEQ_B)
    games.games(AHRI, SYNDRA, 25, SEQ_A, wins=13)
    games.games(AHRI, LUX, 10, SEQ_A)
    games.done()

    answer = core_builds(games.conn, AHRI, "MIDDLE", ZED)

    assert (answer.level, answer.scope, answer.granularity) == (3, "champion", "sequence")
    assert answer.sample_size == 45
    assert answer.label == "all opponents: 45 games"
    assert _ids(answer.rows[0]) == SEQ_A and answer.rows[0].games == 35
    assert "all opponents" in answer.fallback_note


def test_level_four_champion_per_item(games):
    games.games(AHRI, ZED, 20, SEQ_A)
    games.games(AHRI, SYNDRA, 15, SEQ_B)
    games.done()

    answer = core_builds(games.conn, AHRI, "MIDDLE", ZED)

    assert (answer.level, answer.scope, answer.granularity) == (4, "champion", "item")
    assert answer.sample_size == 35
    assert _ids(answer.rows[0]) == [LUDENS] and answer.rows[0].games == 35
    assert answer.rows[0].sufficient is True


def test_no_level_qualifies_returns_level_four_with_insufficient_rows(games):
    games.games(AHRI, ZED, 5, SEQ_A, wins=5)
    games.done()

    answer = core_builds(games.conn, AHRI, "MIDDLE", ZED)

    assert answer.level == 4 and answer.fell_back is True
    assert answer.answered is False
    assert answer.label == "all opponents: 5 games"
    assert answer.fallback_note == ("No build reaches 30 games at any level; "
                                    "showing what exists for Ahri, mid, all opponents")
    assert len(answer.rows) == 3
    assert all(not r.sufficient for r in answer.rows)


def test_champion_with_no_games_returns_empty_level_four(games):
    games.done()

    answer = core_builds(games.conn, ORIANNA, "MIDDLE", ZED)

    assert (answer.level, answer.sample_size, answer.rows) == (4, 0, ())
    assert answer.answered is False
    assert answer.label == "all opponents: 0 games"
    assert answer.fallback_note == "No Orianna games at mid in this dataset"


def test_no_games_at_bot_says_so(games):
    games.games(AHRI, ZED, 35, SEQ_A)
    games.done()

    answer = core_builds(games.conn, AHRI, "BOTTOM", ZED)

    assert answer.answered is False
    assert answer.fallback_note == "No Ahri games at bot in this dataset"


def test_answered_levels_report_answered_true(games):
    games.games(AHRI, ZED, 20, SEQ_A)
    games.games(AHRI, SYNDRA, 15, SEQ_B)
    games.done()

    assert core_builds(games.conn, AHRI, "MIDDLE", ZED).answered is True


def test_rows_ranked_by_pick_rate_not_win_rate(games):
    games.games(AHRI, ZED, 31, SEQ_A, wins=5)
    games.games(AHRI, ZED, 6, SEQ_B, wins=6)
    games.done()

    answer = core_builds(games.conn, AHRI, "MIDDLE", ZED)

    assert answer.level == 1
    assert [_ids(r) for r in answer.rows] == [SEQ_A, SEQ_B]
    assert answer.rows[1].win_rate == 1.0 and answer.rows[1].sufficient is False


def test_at_most_five_rows_per_level(games):
    games.games(AHRI, ZED, 30, SEQ_A)
    for third in (ZHONYAS, VOID, BANSHEES, MORELLO, CRYPTBLOOM, DEATHCAP):
        games.games(AHRI, ZED, 1, [LUDENS, SHADOWFLAME, third] if third != DEATHCAP
                    else [SHADOWFLAME, LUDENS, DEATHCAP])
    games.done()

    answer = core_builds(games.conn, AHRI, "MIDDLE", ZED)

    assert answer.level == 1 and len(answer.rows) == 5
    assert answer.sample_size == 36


def test_participants_without_full_core_are_excluded(games):
    games.games(AHRI, ZED, 30, SEQ_A)
    games.games(AHRI, ZED, 10, [LUDENS, SHADOWFLAME])
    games.done()

    answer = core_builds(games.conn, AHRI, "MIDDLE", ZED)

    assert answer.sample_size == 30 and answer.rows[0].pick_rate == 1.0


def test_role_filter_separates_roles(games):
    games.games(AHRI, ZED, 35, SEQ_A, role="TOP")
    games.done()

    assert core_builds(games.conn, AHRI, "MIDDLE", ZED).rows == ()
    assert core_builds(games.conn, AHRI, "TOP", ZED).level == 1


def test_bad_role_and_unknown_champion_raise_validation_error(games):
    games.done()
    with pytest.raises(ValidationError):
        core_builds(games.conn, AHRI, "ADC", ZED)
    with pytest.raises(ValidationError):
        core_builds(games.conn, 424242, "MIDDLE", ZED)
