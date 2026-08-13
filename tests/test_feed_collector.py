from datetime import datetime, timezone
from pathlib import Path

import pytest

from collectors.feed import collect, strip_html
from collectors.http import FetchError

FIXTURES = Path(__file__).parent / "fixtures"
UTC = timezone.utc


class FixtureSession:
    def __init__(self, filename=None, error=None):
        self.filename = filename
        self.error = error

    def get(self, url, timeout=None, headers=None):
        if self.error:
            raise self.error
        return _FakeResponse((FIXTURES / self.filename).read_bytes())


class _FakeResponse:
    def __init__(self, content):
        self.content = content
        self.status_code = 200

    def raise_for_status(self):
        return None


def source(**overrides):
    base = {
        "name": "Test Source",
        "category": "Test",
        "type": "feed",
        "url": "https://x.test/rss",
    }
    base.update(overrides)
    return base


def test_all_timezone_formats_resolve_to_the_same_utc_instant():
    """The single most important test in the project.

    -0500 at 07:00, Zulu at 12:00, and EDT (-0400) at 08:00 are all the
    same instant. A bug here silently loses or duplicates articles.
    """
    expected = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    for filename in (
        "feed_rfc822_offset.xml",
        "feed_atom_zulu.xml",
        "feed_textual_tz.xml",
    ):
        [article] = collect(source(), FixtureSession(filename))
        assert article.published == expected, filename
        assert article.published.tzinfo is not None, filename


def test_maps_fields_onto_article():
    [article] = collect(
        source(name="Offset", category="News"), FixtureSession("feed_rfc822_offset.xml")
    )
    assert article.source == "Offset"
    assert article.category == "News"
    assert article.headline == "Story with numeric offset"
    assert article.link == "https://offset.test/post-1"


def test_blurb_has_html_stripped():
    [article] = collect(source(), FixtureSession("feed_rfc822_offset.xml"))
    assert article.blurb == "Body text with markup."


def test_missing_date_yields_none():
    [article] = collect(source(), FixtureSession("feed_no_date.xml"))
    assert article.published is None


def test_malformed_feed_with_entries_is_used(caplog):
    with caplog.at_level("WARNING", logger="technews"):
        articles = collect(source(), FixtureSession("feed_malformed_with_entries.xml"))
    assert len(articles) == 1


def test_fetch_failure_propagates():
    with pytest.raises(FetchError):
        collect(source(), FixtureSession(error=__import__("requests").ConnectionError()))


def test_strip_html_truncates_and_collapses_whitespace():
    assert strip_html("<p>a   b</p>\n<p>c</p>") == "a b c"
    assert len(strip_html("x" * 500)) == 200


def test_strip_html_separates_adjacent_block_tags():
    assert strip_html("<p>First para.</p><p>Second para.</p>") == "First para. Second para."
    assert strip_html("<li>one</li><li>two</li>") == "one two"


def test_strip_html_does_not_leave_space_before_punctuation():
    assert strip_html("<p>Body text with <b>markup</b>.</p>") == "Body text with markup."
