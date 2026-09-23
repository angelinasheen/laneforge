"""Rate limiter behaviour under a fake clock (no real sleeping)."""
from __future__ import annotations

from laneforge.ingest.ratelimit import (
    DEV_KEY_WINDOWS,
    RateLimiter,
    Window,
    parse_rate_header,
)


class FakeClock:
    """Time only advances when the limiter sleeps (or a test advances it)."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_limiter(clock: FakeClock, windows=DEV_KEY_WINDOWS) -> RateLimiter:
    return RateLimiter(windows, clock=clock, sleep=clock.sleep)


def test_hundred_calls_never_exceed_two_minute_window():
    # Arrange
    clock = FakeClock()
    limiter = make_limiter(clock)
    stamps: list[float] = []

    # Act
    for _ in range(350):
        limiter.acquire()
        stamps.append(clock.now)

    # Assert: every 120 s interval holds at most 100 calls, every 1 s at most 20
    for i, start in enumerate(stamps):
        in_two_min = sum(1 for t in stamps[i:] if t < start + 120.0)
        in_one_sec = sum(1 for t in stamps[i:] if t < start + 1.0)
        assert in_two_min <= 100
        assert in_one_sec <= 20


def test_hundred_and_first_call_waits_until_window_frees():
    # Arrange
    clock = FakeClock()
    limiter = make_limiter(clock)
    first_stamp = None
    for n in range(100):
        limiter.acquire()
        if n == 0:
            first_stamp = clock.now

    # Act
    limiter.acquire()

    # Assert: the 101st call happens exactly when the first call leaves the window
    assert clock.now == first_stamp + 120.0


def test_twenty_first_call_in_one_second_waits():
    # Arrange
    clock = FakeClock()
    limiter = make_limiter(clock, windows=(Window(20, 1.0),))
    for _ in range(20):
        limiter.acquire()

    # Act
    limiter.acquire()

    # Assert
    assert clock.now == 1.0
    assert clock.sleeps == [1.0]


def test_calls_under_the_limits_do_not_sleep():
    # Arrange
    clock = FakeClock()
    limiter = make_limiter(clock)

    # Act
    for _ in range(20):
        limiter.acquire()

    # Assert
    assert clock.sleeps == []


def test_back_off_sleeps_the_retry_after_seconds():
    # Arrange
    clock = FakeClock()
    limiter = make_limiter(clock)

    # Act
    limiter.back_off(7)

    # Assert
    assert clock.sleeps == [7.0]


def test_server_count_header_pads_local_window():
    # Arrange
    clock = FakeClock()
    limiter = make_limiter(clock)
    two_min = DEV_KEY_WINDOWS[1]

    # Act
    limiter.observe_server_counts("3:1,100:120")

    # Assert: the next acquire must wait for the whole two-minute window
    assert limiter.count_in_window(two_min) == 100
    limiter.acquire()
    assert clock.now == 120.0


def test_parse_rate_header_ignores_malformed_pairs():
    assert parse_rate_header("18:1,95:120") == {1: 18, 120: 95}
    assert parse_rate_header("x:1,5:120") == {120: 5}
    assert parse_rate_header(None) == {}
