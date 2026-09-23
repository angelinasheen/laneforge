import pytest

from laneforge.queries.stats import wilson_interval


def test_wilson_54_of_100_matches_known_interval():
    low, high = wilson_interval(54, 100)
    assert low == pytest.approx(0.4426, abs=1e-3)
    assert high == pytest.approx(0.6344, abs=1e-3)


def test_wilson_zero_games_is_zero_interval():
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_wilson_all_wins_stays_within_unit_interval():
    low, high = wilson_interval(30, 30)
    assert 0.88 < low < 1.0 and high == pytest.approx(1.0)


def test_wilson_zero_wins_starts_at_zero():
    low, high = wilson_interval(0, 30)
    assert low == pytest.approx(0.0) and high == pytest.approx(0.1135, abs=1e-3)


def test_wilson_rejects_more_wins_than_games():
    with pytest.raises(ValueError):
        wilson_interval(5, 4)
