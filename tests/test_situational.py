"""Situational items: comp profile, rule thresholds, scores, evidence."""
import pytest

from laneforge.queries.errors import ValidationError
from laneforge.queries.models import CompProfile
from laneforge.queries.rules import pen_rule, triggered_rules
from laneforge.queries.situational import comp_profile, situational_items
from tests.query_fixtures import (
    BANSHEES, DEATHCAP, GameMaker, LUDENS, MERCS, SERYLDA, YOUMUU, seed_catalogue,
)

AHRI, ZED, TALON, LUX = 103, 238, 91, 99
MAGIC_HEAVY = dict(physical_damage=0, magic_damage=20000, true_damage=0)
AHRI_CORE = [LUDENS, BANSHEES, DEATHCAP]


def _profile(**overrides):
    base = dict(magic_share=0.3, physical_share=0.3, true_share=0.4, healing_pm=100.0,
                cc_pm=1.0, healing_pm_p75=100.0, cc_pm_p75=1.0, partial=False, members=())
    return CompProfile(**{**base, **overrides})


def _keys(rules):
    return [r.key for r in rules]


# --- rule thresholds (pure) --------------------------------------------------

def test_magic_rule_triggers_at_exactly_55_percent():
    assert _keys(triggered_rules(_profile(magic_share=0.55))) == ["magic"]
    assert _keys(triggered_rules(_profile(magic_share=0.5499))) == []


def test_physical_rule_triggers_at_exactly_55_percent():
    assert _keys(triggered_rules(_profile(physical_share=0.55))) == ["physical"]
    assert _keys(triggered_rules(_profile(physical_share=0.5499))) == []


def test_healing_and_cc_rules_need_strictly_above_p75():
    assert _keys(triggered_rules(_profile(healing_pm=100.0, cc_pm=1.0))) == []
    assert _keys(triggered_rules(_profile(healing_pm=100.01, cc_pm=1.01))) == ["healing", "cc"]


def test_healing_rule_silent_without_thresholds():
    assert triggered_rules(_profile(healing_pm=1e9, healing_pm_p75=None)) == ()


def test_rules_come_in_fixed_order_with_reasons():
    rules = triggered_rules(_profile(magic_share=0.6, physical_share=0.6, healing_pm=200,
                                     cc_pm=2))
    assert _keys(rules) == ["magic", "physical", "healing", "cc"]
    assert rules[0].reason == "comp is 60% magic damage"
    assert rules[0].item_class == "magic resist"


def test_pen_rule_threshold_is_60_resist_at_level_11():
    from laneforge.queries.models import ChampionRef
    at_60 = ChampionRef(1, "Zed", "Zed", base_armor=30, armor_per_level=3.0)
    below = ChampionRef(1, "Zed", "Zed", base_armor=30, armor_per_level=2.9)
    assert pen_rule(at_60, "physical").reason == "Zed has 60 armor at level 11"
    assert pen_rule(below, "physical") is None


# --- database-backed ---------------------------------------------------------

@pytest.fixture
def games(conn):
    seed_catalogue(conn, {AHRI: "Ahri", ZED: "Zed", TALON: "Talon", LUX: "Lux"})
    return GameMaker(conn)


def test_comp_profile_uses_opponent_role_profile_and_enemy_means(games):
    games.games(AHRI, ZED, 5, AHRI_CORE, red_measures=MAGIC_HEAVY)
    games.done()

    profile = comp_profile(games.conn, "MIDDLE", ZED, [904, LUX])

    assert profile.partial is True
    assert profile.magic_share == pytest.approx(1.0)
    assert [m.source for m in profile.members] == ["champion", "champion", "none"]
    assert profile.members[0].role == "MIDDLE" and profile.members[1].role is None
    assert profile.members[2].magic_pm == 0.0


def test_magic_comp_suggests_mr_items_ordered_by_score(games):
    games.games(AHRI, ZED, 30, AHRI_CORE, wins=18, red_measures=MAGIC_HEAVY)
    games.done()

    answer = situational_items(games.conn, AHRI, "MIDDLE", ZED, [904, 905, 906, 907])

    assert _keys(answer.triggered) == ["magic"]
    assert answer.profile.partial is False
    names = [s.item.item_id for s in answer.suggestions]
    # Ahri at 11: 1600 HP, 45 MR. Mercs +20 MR / 1250 g = 256; Banshee +40 / 3000 g = 213.3
    assert names == [MERCS, BANSHEES]
    assert answer.suggestions[0].score == pytest.approx(256.0)
    assert answer.suggestions[0].score_text == "+256 effective HP per 1,000 gold"
    assert answer.suggestions[1].score == pytest.approx(640 / 3)


def test_evidence_appears_at_30_games(games):
    games.games(AHRI, ZED, 30, AHRI_CORE, wins=18, red_measures=MAGIC_HEAVY)
    games.done()

    answer = situational_items(games.conn, AHRI, "MIDDLE", ZED, [])

    by_item = {s.item.item_id: s for s in answer.suggestions}
    ev = by_item[BANSHEES].evidence
    assert (ev.games, ev.wins) == (30, 18)
    assert ev.win_rate == pytest.approx(0.6)
    assert ev.condition_text == "comps with at least 55% magic damage"
    assert by_item[MERCS].evidence is None
    assert answer.class_evidence["magic"].games == 30


def test_no_evidence_below_30_games(games):
    games.games(AHRI, ZED, 29, AHRI_CORE, red_measures=MAGIC_HEAVY)
    games.done()

    answer = situational_items(games.conn, AHRI, "MIDDLE", ZED, [])

    assert all(s.evidence is None for s in answer.suggestions)
    assert answer.class_evidence == {}


def test_pen_rule_offers_armor_pen_to_ad_champion(games):
    games.games(TALON, ZED, 30, [YOUMUU, SERYLDA], wins=15)
    games.done()

    answer = situational_items(games.conn, TALON, "MIDDLE", ZED, [])

    pen = [s for s in answer.suggestions if s.rule.key == "pen"]
    assert [s.item.item_id for s in pen] == [SERYLDA, YOUMUU]
    assert pen[0].score == pytest.approx(170 / 149 - 1)
    assert pen[0].score_text == "Zed has 70 armor at level 11; raises your damage to Zed by 14%"
    assert pen[0].evidence.games == 30
    assert pen[0].evidence.condition_text == "games against Zed"


def test_ap_champion_gets_magic_pen_check_against_opponent_mr(games):
    games.games(AHRI, ZED, 3, AHRI_CORE)
    games.done()

    answer = situational_items(games.conn, AHRI, "MIDDLE", ZED, [])

    assert "pen" not in _keys(answer.triggered)   # Zed MR at 11 is 45 < 60


def test_situational_rejects_bad_enemy_lists(games):
    games.done()
    with pytest.raises(ValidationError):
        situational_items(games.conn, AHRI, "MIDDLE", ZED, [ZED])
    with pytest.raises(ValidationError):
        situational_items(games.conn, AHRI, "MIDDLE", ZED, [900, 901, 902, 903, 904])
    with pytest.raises(ValidationError):
        situational_items(games.conn, AHRI, "MIDDLE", AHRI, [])
    with pytest.raises(ValidationError):
        situational_items(games.conn, AHRI, "MIDDLE", ZED, [777777])
