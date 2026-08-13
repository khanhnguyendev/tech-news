from datetime import datetime, timezone

from models import Article, normalize_url


def test_normalize_strips_utm_and_trailing_slash():
    url = "https://Example.COM/Path/Post/?utm_source=rss&utm_medium=x&id=7#frag"
    assert normalize_url(url) == "https://example.com/Path/Post?id=7"


def test_normalize_preserves_path_case_and_lowercases_host():
    assert normalize_url("HTTPS://WWW.Site.com/AbC") == "https://www.site.com/AbC"


def test_normalize_drops_click_trackers():
    url = "https://site.com/a?fbclid=1&gclid=2&real=3"
    assert normalize_url(url) == "https://site.com/a?real=3"


def test_normalize_keeps_root_slash():
    assert normalize_url("https://site.com/") == "https://site.com/"


def test_article_id_is_normalized_link():
    a = Article(
        category="Security",
        source="Krebs on Security",
        headline="Something",
        link="https://krebsonsecurity.com/post/?utm_source=feed",
        published=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )
    assert a.id == "https://krebsonsecurity.com/post"


def test_article_is_frozen():
    import dataclasses
    import pytest

    a = Article("c", "s", "h", "https://x.test", None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.headline = "changed"
