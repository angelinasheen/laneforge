"""Client-side rate limiting for the Riot API.

A development or personal key allows 20 requests per second and 100 requests
per two minutes. `RateLimiter` keeps a sliding window of request timestamps
per limit and blocks (via an injectable `sleep`) until every window has room.
The clock and sleep are injectable so tests run instantly with a fake clock.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Window:
    """At most `limit` requests in any `period_s`-second interval."""

    limit: int
    period_s: float


DEV_KEY_WINDOWS: tuple[Window, ...] = (Window(20, 1.0), Window(100, 120.0))

# Riot's X-App-Rate-Limit-Count header looks like "18:1,95:120".
HEADER_PAIR_SEP = ","
HEADER_FIELD_SEP = ":"


class RateLimiter:
    """Sliding-window limiter over several windows at once.

    The timestamp deques are the limiter's private state; nothing outside the
    class sees or shares them.
    """

    def __init__(
        self,
        windows: Iterable[Window] = DEV_KEY_WINDOWS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._windows = tuple(windows)
        if not self._windows:
            raise ValueError("RateLimiter needs at least one window")
        self._clock = clock
        self._sleep = sleep
        self._stamps: dict[Window, deque[float]] = {w: deque() for w in self._windows}

    @property
    def windows(self) -> tuple[Window, ...]:
        return self._windows

    def acquire(self) -> None:
        """Block until a request may be sent, then record it."""
        while True:
            wait = self._required_wait(self._clock())
            if wait <= 0:
                break
            log.debug("rate limiter waiting %.2fs", wait)
            self._sleep(wait)
        now = self._clock()
        for stamps in self._stamps.values():
            stamps.append(now)

    def back_off(self, retry_after_s: float) -> None:
        """Honour a 429: sleep the server-given interval."""
        seconds = max(float(retry_after_s), 0.0)
        log.warning("429 from Riot; sleeping %.1fs (Retry-After)", seconds)
        self._sleep(seconds)

    def observe_server_counts(self, header_value: str | None) -> None:
        """Opportunistically sync with X-App-Rate-Limit-Count.

        If the server has counted more requests in a window than we have
        (another process using the same key, or a restart), pad our window
        with synthetic 'now' stamps so we slow down accordingly.
        """
        counts = parse_rate_header(header_value)
        now = self._clock()
        for window in self._windows:
            server_count = counts.get(int(window.period_s))
            if server_count is None:
                continue
            stamps = self._stamps[window]
            self._prune(stamps, window, now)
            missing = server_count - len(stamps)
            if missing > 0:
                stamps.extend([now] * missing)

    def count_in_window(self, window: Window) -> int:
        stamps = self._stamps[window]
        self._prune(stamps, window, self._clock())
        return len(stamps)

    def _required_wait(self, now: float) -> float:
        wait = 0.0
        for window, stamps in self._stamps.items():
            self._prune(stamps, window, now)
            if len(stamps) >= window.limit:
                # The oldest stamp that must expire before we have room.
                blocker = stamps[len(stamps) - window.limit]
                wait = max(wait, blocker + window.period_s - now)
        return wait

    @staticmethod
    def _prune(stamps: deque[float], window: Window, now: float) -> None:
        while stamps and stamps[0] + window.period_s <= now:
            stamps.popleft()


def parse_rate_header(value: str | None) -> dict[int, int]:
    """'18:1,95:120' -> {1: 18, 120: 95}. Malformed pairs are ignored with a log line."""
    if not value:
        return {}
    result: dict[int, int] = {}
    for pair in value.split(HEADER_PAIR_SEP):
        parts = pair.strip().split(HEADER_FIELD_SEP)
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            log.debug("ignoring malformed rate-limit header pair %r", pair)
            continue
        count, period = int(parts[0]), int(parts[1])
        result[period] = count
    return result
