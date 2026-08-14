"""One HTTP client for every collector: fixed UA, timeout, and single retry."""

from __future__ import annotations

import time
from urllib.parse import urlsplit

import requests

from models import HTTP_TIMEOUT, USER_AGENT, log

RETRY_DELAY_SECONDS = 2.0
MIN_HOST_INTERVAL_SECONDS = 1.5


class HostThrottle:
    """Keeps a minimum gap between requests to the same host.

    Five YouTube channel feeds fired back to back drew three 404s and a
    500 -- interleaved with one success, which is what rules out bad
    channel ids and points at throttling instead. Those three sources were
    then lost for the whole day, because a 404 is deliberately not
    retried: for every other source a 404 really is an answer rather than
    a hiccup, and retrying it would make a deleted feed look like a
    transient blip forever. Spacing the requests treats the cause instead.

    The clock and sleeper are injected so the behaviour can be tested
    without real time. Only the session built by make_session() carries a
    throttle, so test fakes never sleep for spacing.
    """

    def __init__(self, min_interval: float, *, clock=time.monotonic, sleeper=time.sleep):
        self.min_interval = min_interval
        self._clock = clock
        self._sleeper = sleeper
        self._last: dict[str, float] = {}

    def wait(self, url: str) -> None:
        host = urlsplit(url).netloc
        previous = self._last.get(host)
        now = self._clock()
        if previous is not None:
            remaining = self.min_interval - (now - previous)
            if remaining > 0:
                log.debug("Spacing request to %s by %.1fs", host, remaining)
                self._sleeper(remaining)
                now = self._clock()
        self._last[host] = now


class FetchError(Exception):
    """Any failure to retrieve a URL after retrying."""


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    session.throttle = HostThrottle(MIN_HOST_INTERVAL_SECONDS)
    return session


def _get(session, url: str, headers: dict | None):
    """Perform one GET, retrying once on connection errors and 5xx."""
    last_error: Exception | None = None
    throttle = getattr(session, "throttle", None)
    for attempt in (1, 2):
        try:
            if throttle is not None:
                throttle.wait(url)
            response = session.get(url, timeout=HTTP_TIMEOUT, headers=headers)
            if response.status_code >= 500:
                raise requests.HTTPError(
                    f"server error {response.status_code}", response=response
                )
            response.raise_for_status()
            return response
        except requests.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and 400 <= status < 500:
                raise FetchError(f"{url} returned {status}") from exc
            last_error = exc
        except requests.RequestException as exc:
            last_error = exc
        if attempt == 1:
            log.debug("Retrying %s after error: %s", url, last_error)
            time.sleep(RETRY_DELAY_SECONDS)
    raise FetchError(f"{url} failed after retry: {last_error}") from last_error


def fetch(session, url: str, *, headers: dict | None = None) -> bytes:
    return _get(session, url, headers).content


def fetch_json(session, url: str, *, headers: dict | None = None):
    return _get(session, url, headers).json()
