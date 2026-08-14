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


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def throttle(interval=1.5):
    from collectors.http import HostThrottle

    clock = FakeClock()
    return HostThrottle(interval, clock=clock.time, sleeper=clock.sleep), clock


def test_first_request_to_a_host_does_not_wait():
    t, clock = throttle()
    t.wait("https://www.youtube.com/feeds/videos.xml?channel_id=A")
    assert clock.slept == []


def test_a_second_request_to_the_same_host_waits_the_remainder():
    """Five YouTube feeds fired back to back got throttled by YouTube into
    404s and a 500 -- interleaved with a success, which is what rules out
    bad channel ids. Spacing them is the fix aimed at the cause."""
    t, clock = throttle(1.5)
    t.wait("https://www.youtube.com/feeds/videos.xml?channel_id=A")
    clock.now += 0.4
    t.wait("https://www.youtube.com/feeds/videos.xml?channel_id=B")
    assert clock.slept == [pytest.approx(1.1)]


def test_a_different_host_is_not_delayed():
    t, clock = throttle()
    t.wait("https://www.youtube.com/feeds/videos.xml")
    t.wait("https://krebsonsecurity.com/feed/")
    assert clock.slept == []


def test_no_wait_once_the_interval_has_already_passed():
    t, clock = throttle(1.5)
    t.wait("https://www.youtube.com/a")
    clock.now += 5.0
    t.wait("https://www.youtube.com/b")
    assert clock.slept == []


def test_three_rapid_requests_are_spaced_cumulatively():
    """Each wait records the time it finished, so requests do not all
    bunch up against the first one's timestamp."""
    t, clock = throttle(1.5)
    for path in "abc":
        t.wait(f"https://www.youtube.com/{path}")
    assert clock.slept == [pytest.approx(1.5), pytest.approx(1.5)]
    assert clock.now == pytest.approx(3.0)


def test_make_session_attaches_a_throttle():
    from collectors.http import HostThrottle

    assert isinstance(getattr(make_session(), "throttle", None), HostThrottle)


def test_a_session_without_a_throttle_is_not_delayed():
    """Test fakes have no throttle attribute, so the suite never sleeps for
    spacing -- only the real session built by make_session() does."""
    session = FakeSession([FakeResponse(content=b"ok")])
    assert fetch(session, "https://x.test/a") == b"ok"
