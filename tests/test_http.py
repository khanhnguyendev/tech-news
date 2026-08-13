import pytest
import requests

from collectors.http import FetchError, fetch, fetch_json, make_session


class FakeResponse:
    def __init__(self, status_code=200, content=b"", json_data=None):
        self.status_code = status_code
        self.content = content
        self._json = json_data

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)


class FakeSession:
    """Returns queued responses or raises queued exceptions, recording calls."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append(url)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_make_session_sets_user_agent():
    session = make_session()
    assert "TechNews" in session.headers["User-Agent"]


def test_fetch_returns_body():
    session = FakeSession([FakeResponse(content=b"<rss/>")])
    assert fetch(session, "https://x.test/rss") == b"<rss/>"


def test_fetch_retries_once_on_connection_error():
    session = FakeSession(
        [requests.ConnectionError("boom"), FakeResponse(content=b"ok")]
    )
    assert fetch(session, "https://x.test/rss") == b"ok"
    assert len(session.calls) == 2


def test_fetch_retries_once_on_server_error():
    session = FakeSession([FakeResponse(status_code=503), FakeResponse(content=b"ok")])
    assert fetch(session, "https://x.test/rss") == b"ok"


def test_fetch_does_not_retry_on_client_error():
    session = FakeSession([FakeResponse(status_code=404)])
    with pytest.raises(FetchError):
        fetch(session, "https://x.test/rss")
    assert len(session.calls) == 1


def test_fetch_gives_up_after_one_retry():
    session = FakeSession(
        [requests.ConnectionError("a"), requests.ConnectionError("b")]
    )
    with pytest.raises(FetchError):
        fetch(session, "https://x.test/rss")
    assert len(session.calls) == 2


def test_fetch_json_parses_body():
    session = FakeSession([FakeResponse(json_data=[{"tag_name": "v1"}])])
    assert fetch_json(session, "https://api.test/releases")[0]["tag_name"] == "v1"


def test_fetch_failure_preserves_the_underlying_cause():
    original = requests.ConnectionError("boom")
    session = FakeSession([original, requests.ConnectionError("boom again")])
    with pytest.raises(FetchError) as excinfo:
        fetch(session, "https://x.test/rss")
    assert excinfo.value.__cause__ is not None
    assert isinstance(excinfo.value.__cause__, requests.RequestException)
