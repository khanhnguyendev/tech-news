from pathlib import Path

import pytest

from collectors.github_trending import collect, format_stars

FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, content):
        self.content = content
        self.status_code = 200

    def raise_for_status(self):
        return None


class FixtureSession:
    def __init__(self, filename="github_trending.html"):
        self.filename = filename
        self.calls = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append(url)
        return FakeResponse((FIXTURES / self.filename).read_bytes())


def source(**overrides):
    base = {
        "name": "GitHub Trending",
        "category": "Trending",
        "type": "github_trending",
        "gate": "new_only",
    }
    base.update(overrides)
    return base


def test_collects_one_article_per_repo():
    articles = collect(source(), FixtureSession())
    assert [a.headline for a in articles] == [
        "cathrynlavery/diagram-design",
        "anthropics/skills",
        "kepano/obsidian-skills",
    ]


def test_headline_has_no_stray_whitespace():
    """GitHub renders the name as `owner / repo` across several elements;
    the digest should show the canonical `owner/repo`."""
    articles = collect(source(), FixtureSession())
    assert all(" " not in a.headline for a in articles)


def test_link_is_the_absolute_repo_url():
    [first, *_] = collect(source(), FixtureSession())
    assert first.link == "https://github.com/cathrynlavery/diagram-design"


def test_blurb_carries_metrics_then_description():
    """The metrics are what answer 'is this notable', so they lead. The
    description sits on its own line beneath them."""
    [first, *_] = collect(source(), FixtureSession())
    metrics, description = first.blurb.split("\n")
    assert "15.7k" in metrics
    assert "+4,475 today" in metrics
    assert "HTML" in metrics
    assert description.startswith("29 editorial diagram types")


def test_missing_language_is_omitted_without_a_dangling_separator():
    articles = collect(source(), FixtureSession())
    obsidian = [a for a in articles if a.headline == "kepano/obsidian-skills"][0]
    metrics = obsidian.blurb.split("\n")[0]
    assert "46.1k" in metrics
    assert "+292 today" in metrics
    assert not metrics.rstrip().endswith("·")
    assert "··" not in metrics


def test_published_is_none_so_the_new_only_gate_applies():
    """A trending listing has no publication date. The source runs with
    gate: new_only, and dedup by repo URL is what keeps it from repeating."""
    assert all(a.published is None for a in collect(source(), FixtureSession()))


def test_category_and_source_come_from_config():
    [first, *_] = collect(source(name="Trending Repos", category="OSS"), FixtureSession())
    assert first.source == "Trending Repos"
    assert first.category == "OSS"


def test_requests_the_trending_page_by_default():
    session = FixtureSession()
    collect(source(), session)
    assert session.calls == ["https://github.com/trending"]


def test_a_configured_url_wins():
    """So a language- or period-scoped listing can be configured without
    code changes, e.g. /trending/python?since=weekly."""
    session = FixtureSession()
    collect(source(url="https://github.com/trending/rust?since=weekly"), session)
    assert session.calls == ["https://github.com/trending/rust?since=weekly"]


def test_no_rows_raises_rather_than_reporting_an_empty_day():
    """GitHub changing its markup must be loud. An empty list would be
    indistinguishable from a quiet day and would never reach the digest's
    failure footer."""
    with pytest.raises(ValueError, match="no repositories"):
        collect(source(), FixtureSession("events_page.html"))


@pytest.mark.parametrize(
    "count,expected",
    [
        (0, "0"),
        (999, "999"),
        (1000, "1.0k"),
        (15694, "15.7k"),
        (46051, "46.1k"),
        (169247, "169k"),
        (1200000, "1.2M"),
    ],
)
def test_format_stars(count, expected):
    assert format_stars(count) == expected
