"""A small Riot API client: rate limited, retrying, with typed errors.

Only the endpoints the crawler needs are wrapped (see the URL helpers at the
bottom). The API key is passed in; the CLI reads it from `RIOT_API_KEY`.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Mapping

import httpx

from laneforge.ingest.ratelimit import RateLimiter

log = logging.getLogger(__name__)

PLATFORM_HOST = "https://na1.api.riotgames.com"
REGION_HOST = "https://americas.api.riotgames.com"
RANKED_SOLO_QUEUE = "RANKED_SOLO_5x5"
RANKED_SOLO_QUEUE_ID = 420

SERVER_ERROR_ATTEMPTS = 3
SERVER_ERROR_BASE_DELAY_S = 1.0
MAX_429_RETRIES = 5
DEFAULT_RETRY_AFTER_S = 10.0
NETWORK_ERROR_ATTEMPTS = 6          # 5 + 10 + 20 + 40 + 80 s of waiting before giving up
NETWORK_ERROR_BASE_DELAY_S = 5.0
REQUEST_TIMEOUT_S = 30.0

AUTH_HELP = (
    "Riot rejected the API key ({status}). Development keys expire every 24 hours: "
    "regenerate it at https://developer.riotgames.com, put it in .env as RIOT_API_KEY=..., "
    "and rerun the same command; the crawl resumes from data/checkpoint.json."
)


class RiotError(Exception):
    """Any failure talking to the Riot API."""


class RiotAuthError(RiotError):
    """401/403: the key is missing, invalid, or expired."""


class NotFound(RiotError):
    """404: the resource does not exist (e.g. a timeline that was never stored)."""


class RiotNetworkError(RiotError):
    """DNS failure, connection reset, timeout: the request never got an answer."""


class RiotServerError(RiotError):
    """5xx that persisted through every retry, or repeated 429s."""


class RiotClient:
    def __init__(
        self,
        api_key: str,
        limiter: RateLimiter,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise RiotAuthError(AUTH_HELP.format(status="no RIOT_API_KEY set"))
        self._limiter = limiter
        self._sleep = sleep
        self._http = httpx.Client(
            headers={"X-Riot-Token": api_key},
            timeout=REQUEST_TIMEOUT_S,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "RiotClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def get_json(self, url: str, params: Mapping[str, Any] | None = None) -> Any:
        """GET `url` and return the decoded JSON body.

        Raises RiotAuthError (401/403), NotFound (404), RiotServerError (5xx
        after retries, or too many 429s), RiotError (any other status).
        """
        server_failures = 0
        throttles = 0
        network_failures = 0
        while True:
            self._limiter.acquire()
            try:
                response = self._send(url, params)
            except RiotNetworkError as exc:
                # DNS blips and dropped connections happen over a multi-hour
                # crawl; wait with growing patience, then stop cleanly so the
                # checkpoint can resume the run later.
                network_failures += 1
                if network_failures >= NETWORK_ERROR_ATTEMPTS:
                    raise RiotServerError(
                        f"network still failing after {network_failures} attempts: {exc}") from exc
                delay = NETWORK_ERROR_BASE_DELAY_S * 2 ** (network_failures - 1)
                log.warning("%s; retrying in %.0fs", exc, delay)
                self._sleep(delay)
                continue
            self._limiter.observe_server_counts(response.headers.get("X-App-Rate-Limit-Count"))
            status = response.status_code
            if status == 200:
                return _decode(response)
            if status in (401, 403):
                raise RiotAuthError(AUTH_HELP.format(status=f"HTTP {status}"))
            if status == 404:
                raise NotFound(f"404 for {url}")
            if status == 429:
                throttles += 1
                if throttles > MAX_429_RETRIES:
                    raise RiotServerError(f"still throttled after {MAX_429_RETRIES} retries: {url}")
                self._limiter.back_off(_retry_after(response))
                continue
            if 500 <= status < 600:
                server_failures += 1
                if server_failures >= SERVER_ERROR_ATTEMPTS:
                    raise RiotServerError(f"HTTP {status} after {server_failures} attempts: {url}")
                delay = SERVER_ERROR_BASE_DELAY_S * 2 ** (server_failures - 1)
                log.warning("HTTP %s from %s; retrying in %.0fs", status, url, delay)
                self._sleep(delay)
                continue
            raise RiotError(f"unexpected HTTP {status} for {url}: {response.text[:200]}")

    def _send(self, url: str, params: Mapping[str, Any] | None) -> httpx.Response:
        try:
            return self._http.get(url, params=dict(params or {}))
        except httpx.TransportError as exc:
            raise RiotNetworkError(f"network error for {url}: {exc}") from exc


def _decode(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise RiotError(f"invalid JSON from {response.request.url}") from exc


def _retry_after(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After")
    try:
        return float(raw) if raw is not None else DEFAULT_RETRY_AFTER_S
    except ValueError:
        log.warning("unparseable Retry-After %r; using %.0fs", raw, DEFAULT_RETRY_AFTER_S)
        return DEFAULT_RETRY_AFTER_S


# --- endpoint URLs -----------------------------------------------------------

def league_entries_url(tier: str, division: str) -> str:
    return f"{PLATFORM_HOST}/lol/league/v4/entries/{RANKED_SOLO_QUEUE}/{tier}/{division}"


def match_ids_url(puuid: str) -> str:
    return f"{REGION_HOST}/lol/match/v5/matches/by-puuid/{puuid}/ids"


def match_url(match_id: str) -> str:
    return f"{REGION_HOST}/lol/match/v5/matches/{match_id}"


def timeline_url(match_id: str) -> str:
    return f"{REGION_HOST}/lol/match/v5/matches/{match_id}/timeline"
