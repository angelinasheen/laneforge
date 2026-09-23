import pytest

from laneforge.queries.models import ChampionRef, CompProfile, ItemRef
from laneforge.queries.statmodel import (
    damage_multiplier, defensive_score, effective_hp, penetration_score, resist_at_level,
)

# Factory defaults: 600 HP + 100/level, 30 armor + 4/level, 30 MR + 1.5/level.
CHAMP = ChampionRef(champion_id=1, name="Ahri", ddragon_key="Ahri", base_health=600,
                    health_per_level=100, base_armor=30, armor_per_level=4,
                    base_magic_resist=30, magic_resist_per_level=1.5)
ZED = ChampionRef(champion_id=238, name="Zed", ddragon_key="Zed", base_health=654,
                  health_per_level=99, base_armor=32, armor_per_level=4.2,
                  base_magic_resist=29, magic_resist_per_level=2.05)


def _profile(physical, magic):
    return CompProfile(magic_share=magic, physical_share=physical,
                       true_share=1 - physical - magic, healing_pm=0, cc_pm=0,
                       healing_pm_p75=None, cc_pm_p75=None, partial=False, members=())


def _item(**stats):
    base = dict(item_id=1, name="X", gold_cost=3000, is_legendary=True, is_boots=False)
    return ItemRef(**{**base, **stats})


def test_resist_at_level_eleven_adds_ten_growths():
    assert resist_at_level(32, 4.2, 11) == pytest.approx(74.0)


def test_damage_multiplier_is_hundred_over_hundred_plus_resist():
    assert damage_multiplier(100) == pytest.approx(0.5)
    assert damage_multiplier(0) == pytest.approx(1.0)


def test_effective_hp_treats_remainder_as_true_damage():
    # 1600 * 1.7 * .4 + 1600 * 1.45 * .5 + 1600 * .1 = 1088 + 1160 + 160
    assert effective_hp(1600, 70, 45, 0.4, 0.5) == pytest.approx(2408.0)


def test_defensive_score_pure_magic_comp_hand_computed():
    # level 11: 1600 HP, 45 MR. +50 MR: 1600*1.95 - 1600*1.45 = 800 eHP for 2500 gold.
    item = _item(magic_resist=50, gold_cost=2500)
    assert defensive_score(CHAMP, item, _profile(0.0, 1.0)) == pytest.approx(320.0)


def test_defensive_score_mixed_comp_with_health_and_armor():
    # before 2408 (above); after 1800*2.1*.4 + 1800*1.45*.5 + 1800*.1 = 2997; +589 / 3000g
    item = _item(armor=40, health=200, gold_cost=3000)
    assert defensive_score(CHAMP, item, _profile(0.4, 0.5)) == pytest.approx(589 / 3.0)


def test_defensive_score_is_zero_for_free_item():
    assert defensive_score(CHAMP, _item(armor=10, gold_cost=0), _profile(1.0, 0.0)) == 0.0


def test_lethality_against_zed_level_eleven():
    # Zed armor 74 -> 56 after 18 lethality: 174/156 - 1
    assert penetration_score(_item(lethality=18), ZED) == pytest.approx(174 / 156 - 1)


def test_percent_armor_pen_against_zed():
    # 74 * 0.7 = 51.8 -> 174/151.8 - 1 = 0.146
    assert penetration_score(_item(armor_pen_pct=0.3), ZED) == pytest.approx(174 / 151.8 - 1)


def test_magic_pen_uses_magic_resist():
    # Zed MR at 11 = 29 + 20.5 = 49.5; 40% pen -> 29.7
    score = penetration_score(_item(magic_pen_pct=0.4), ZED, kind="magic")
    assert score == pytest.approx(149.5 / 129.7 - 1)


def test_penetration_never_drops_resist_below_zero():
    assert penetration_score(_item(lethality=500), ZED) == pytest.approx(174 / 100 - 1)
