"""One HTTP client for every collector: fixed UA, timeout, and single retry."""

from __future__ import annotations

import time

import requests

from models import HTTP_TIMEOUT, USER_AGENT, log

RETRY_DELAY_SECONDS = 2.0


class FetchError(Exception):
    """Any failure to retrieve a URL after retrying."""


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def _get(session, url: str, headers: dict | None):
    """Perform one GET, retrying once on connection errors and 5xx."""
    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
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
    raise FetchError(f"{url} failed after retry: {last_error}")


def fetch(session, url: str, *, headers: dict | None = None) -> bytes:
    return _get(session, url, headers).content


def fetch_json(session, url: str, *, headers: dict | None = None):
    return _get(session, url, headers).json()
