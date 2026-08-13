from datetime import datetime, timezone
from pathlib import Path

from collectors.html_scrape import collect, parse_time_attribute
from settings import load_config

FIXTURES = Path(__file__).parent / "fixtures"
CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
UTC = timezone.utc


class FakeResponse:
    def __init__(self, content):
        self.content = content
        self.status_code = 200

    def raise_for_status(self):
        return None


class FixtureSession:
    def __init__(self, filename):
        self.filename = filename

    def get(self, url, timeout=None, headers=None):
        return FakeResponse((FIXTURES / self.filename).read_bytes())


def source(**overrides):
    base = {
        "name": "Anthropic Events",
        "category": "Anthropic",
        "type": "html",
        "url": "https://www.anthropic.com/events",
        "gate": "new_only",
        "selectors": {
            "item": "li.event-card",
            "title": "h3.event-title",
            "link": "a.event-link",
            "date": "time.event-date",
            "blurb": "p.event-blurb",
        },
    }
    base.update(overrides)
    return base


def test_extracts_one_article_per_item():
    articles = collect(source(), FixtureSession("events_page.html"))
    assert [a.headline for a in articles] == [
        "Anthropic Dev Day 2026",
        "Partner Webinar",
    ]


def test_relative_links_are_resolved_against_the_page_url():
    articles = collect(source(), FixtureSession("events_page.html"))
    # Relative path href should be joined with the page URL
    assert articles[0].link == "https://www.anthropic.com/events/dev-day-2026"


def test_absolute_links_are_left_alone():
    articles = collect(source(), FixtureSession("events_page.html"))
    assert articles[1].link == "https://external.test/webinar"


def test_machine_readable_date_is_parsed_to_utc():
    articles = collect(source(), FixtureSession("events_page.html"))
    assert articles[0].published == datetime(2026, 9, 15, 17, 0, tzinfo=UTC)


def test_unparseable_date_becomes_none():
    articles = collect(source(), FixtureSession("events_page.html"))
    assert articles[1].published is None


def test_blurb_is_optional():
    articles = collect(source(), FixtureSession("events_page.html"))
    assert articles[0].blurb.startswith("A day of talks")
    assert articles[1].blurb == ""


def test_no_matching_items_raises():
    import pytest

    bad = source(selectors={"item": "div.nothing", "title": "h3", "link": "a"})
    with pytest.raises(ValueError, match="no items"):
        collect(bad, FixtureSession("events_page.html"))


def test_items_matched_but_all_skipped_raises():
    """A page that responds normally, where the item selector still finds
    the <li> cards but the title selector no longer matches anything
    inside them (e.g. a Next.js CSS-module hash changed), must not look
    like a quiet, empty day. Every item gets skipped with a warning, but
    that is a scraper break -- the fix for Task 14's failure footer only
    fires when collect_all sees a failed source, and returning [] here
    would have counted as success."""
    import pytest

    bad = source(
        selectors={
            "item": "li.event-card",
            "title": "h9.does-not-exist",
            "link": "a.event-link",
        }
    )
    with pytest.raises(ValueError, match="matched 2 item"):
        collect(bad, FixtureSession("events_page.html"))


def test_missing_required_selector_raises():
    import pytest

    for missing in ("item", "title", "link"):
        selectors = {
            "item": "li.event-card",
            "title": "h3.event-title",
            "link": "a.event-link",
        }
        del selectors[missing]
        with pytest.raises(ValueError, match=f"selectors.{missing}"):
            collect(source(selectors=selectors), FixtureSession("events_page.html"))


def test_real_events_page_yields_at_least_one_article():
    """tests/fixtures/events_page_real.html is a live snapshot of
    https://www.anthropic.com/events, saved by scripts/verify_sources.py on
    2026-08-13. It is the real-world contract for the "Anthropic Events"
    selectors declared in the shipped config.yaml, alongside the synthetic
    fixture exercised by the tests above -- if Anthropic reshapes the page,
    this test (not just the synthetic one) should fail.
    """
    config = load_config(CONFIG_PATH)
    events_source = next(
        s for s in config["sources"] if s["name"] == "Anthropic Events"
    )
    articles = collect(events_source, FixtureSession("events_page_real.html"))
    assert len(articles) >= 1
    assert all(a.headline and a.link for a in articles)


def test_parse_time_attribute_handles_offsets_and_junk():
    assert parse_time_attribute("2026-09-15T17:00:00+02:00") == datetime(
        2026, 9, 15, 15, 0, tzinfo=UTC
    )
    # Date without time defaults to midnight UTC
    assert parse_time_attribute("2026-09-15") == datetime(2026, 9, 15, 0, 0, tzinfo=UTC)
    assert parse_time_attribute("TBA") is None
    assert parse_time_attribute("") is None
