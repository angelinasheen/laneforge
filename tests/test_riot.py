"""Riot client behaviour against httpx.MockTransport (no network)."""
from __future__ import annotations

import httpx
import pytest

from laneforge.ingest.ratelimit import RateLimiter
from laneforge.ingest.riot import (
    NotFound,
    RiotAuthError,
    RiotClient,
    RiotServerError,
    match_url,
)


class Recorder:
    def __init__(self) -> None:
        self.sleeps: list[float] = []
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(responses: list[httpx.Response], recorder: Recorder, seen: list[httpx.Request]):
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    limiter = RateLimiter(clock=recorder.clock, sleep=recorder.sleep)
    return RiotClient("RGAPI-test", limiter, transport=httpx.MockTransport(handler),
                      sleep=recorder.sleep)


def test_get_json_sends_key_and_params_and_decodes_body():
    # Arrange
    seen: list[httpx.Request] = []
    client = make_client([httpx.Response(200, json={"ok": 1})], Recorder(), seen)

    # Act
    body = client.get_json(match_url("NA1_1"), {"queue": 420})

    # Assert
    assert body == {"ok": 1}
    assert seen[0].headers["X-Riot-Token"] == "RGAPI-test"
    assert seen[0].url.params["queue"] == "420"


def test_403_raises_auth_error_telling_user_to_renew_key():
    client = make_client([httpx.Response(403)], Recorder(), [])

    with pytest.raises(RiotAuthError, match="RIOT_API_KEY"):
        client.get_json(match_url("NA1_1"))


def test_404_raises_not_found():
    client = make_client([httpx.Response(404)], Recorder(), [])

    with pytest.raises(NotFound):
        client.get_json(match_url("NA1_1"))


def test_429_sleeps_retry_after_then_retries():
    # Arrange
    recorder = Recorder()
    seen: list[httpx.Request] = []
    client = make_client(
        [httpx.Response(429, headers={"Retry-After": "5"}), httpx.Response(200, json=[])],
        recorder, seen,
    )

    # Act
    body = client.get_json(match_url("NA1_1"))

    # Assert
    assert body == []
    assert recorder.sleeps == [5.0]
    assert len(seen) == 2


def test_5xx_retries_with_exponential_delay_then_succeeds():
    recorder = Recorder()
    client = make_client(
        [httpx.Response(503), httpx.Response(500), httpx.Response(200, json={"a": 1})],
        recorder, [],
    )

    assert client.get_json(match_url("NA1_1")) == {"a": 1}
    assert recorder.sleeps == [1.0, 2.0]


def test_5xx_three_times_raises_server_error():
    client = make_client([httpx.Response(502)] * 3, Recorder(), [])

    with pytest.raises(RiotServerError):
        client.get_json(match_url("NA1_1"))


def test_missing_key_is_an_auth_error():
    with pytest.raises(RiotAuthError):
        RiotClient("", RateLimiter())


def test_network_error_retries_with_backoff_then_succeeds():
    # Arrange: two DNS-style transport failures, then a good answer.
    recorder = Recorder()
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) < 3:
            raise httpx.ConnectError("nodename nor servname provided", request=request)
        return httpx.Response(200, json={"ok": True})

    limiter = RateLimiter(clock=recorder.clock, sleep=recorder.sleep)
    client = RiotClient("RGAPI-test", limiter, transport=httpx.MockTransport(handler),
                        sleep=recorder.sleep)

    # Act
    body = client.get_json(match_url("NA1_1"))

    # Assert
    assert body == {"ok": True}
    assert recorder.sleeps == [5.0, 10.0]


def test_persistent_network_error_ends_as_a_resumable_server_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    recorder = Recorder()
    limiter = RateLimiter(clock=recorder.clock, sleep=recorder.sleep)
    client = RiotClient("RGAPI-test", limiter, transport=httpx.MockTransport(handler),
                        sleep=recorder.sleep)

    with pytest.raises(RiotServerError):
        client.get_json(match_url("NA1_1"))
    assert len(recorder.sleeps) == 5
