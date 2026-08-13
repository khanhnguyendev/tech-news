import json
import os
from datetime import datetime, timezone
from pathlib import Path

from collectors.github_release import collect

FIXTURES = Path(__file__).parent / "fixtures"
UTC = timezone.utc


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


class RecordingSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append((url, headers or {}))
        return FakeResponse(self.payload)


def payload():
    return json.loads((FIXTURES / "github_releases.json").read_text())


def source(**overrides):
    base = {
        "name": "Playwright",
        "category": "Releases",
        "type": "github_release",
        "repo": "microsoft/playwright",
    }
    base.update(overrides)
    return base


def test_excludes_drafts_and_prereleases_by_default():
    articles = collect(source(), RecordingSession(payload()))
    assert [a.headline for a in articles] == ["Playwright v1.60.0"]


def test_includes_prereleases_when_configured():
    articles = collect(
        source(include_prereleases=True), RecordingSession(payload())
    )
    assert len(articles) == 2


def test_falls_back_to_tag_name_when_name_is_blank():
    articles = collect(
        source(include_prereleases=True), RecordingSession(payload())
    )
    assert "v1.61.0-alpha.1" in [a.headline for a in articles]


def test_parses_published_at_as_utc():
    [article] = collect(source(), RecordingSession(payload()))
    assert article.published == datetime(2026, 8, 12, 10, 0, tzinfo=UTC)


def test_blurb_is_truncated_release_body():
    [article] = collect(source(), RecordingSession(payload()))
    assert article.blurb.startswith("### Highlights")
    assert len(article.blurb) <= 200


def test_requests_the_right_url():
    session = RecordingSession(payload())
    collect(source(), session)
    assert session.calls[0][0] == (
        "https://api.github.com/repos/microsoft/playwright/releases"
    )


def test_sends_auth_header_when_token_present(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    session = RecordingSession(payload())
    collect(source(), session)
    assert session.calls[0][1]["Authorization"] == "Bearer secret-token"


def test_omits_auth_header_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    session = RecordingSession(payload())
    collect(source(), session)
    assert "Authorization" not in session.calls[0][1]
