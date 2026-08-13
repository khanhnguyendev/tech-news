import logging
from datetime import datetime, timezone

from models import Article, log, log_file, normalize_url, setup_logging


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


def test_setup_logging_writes_to_the_current_data_dir(tmp_path, monkeypatch):
    """setup_logging() had no direct test at all before this: nothing
    pinned that it resolves log_file() at call time (not import time) or
    that it actually produces a readable log file where TECHNEWS_DATA_DIR
    currently points."""
    monkeypatch.setenv("TECHNEWS_DATA_DIR", str(tmp_path))
    try:
        setup_logging()
        log.info("hello from test_setup_logging")
        for handler in log.handlers:
            handler.flush()
        assert log_file() == tmp_path / "app.log"
        assert log_file().exists()
        assert "hello from test_setup_logging" in log_file().read_text()
    finally:
        for handler in log.handlers:
            handler.close()
        log.handlers.clear()


def test_setup_logging_verbose_flag_controls_level(tmp_path, monkeypatch):
    monkeypatch.setenv("TECHNEWS_DATA_DIR", str(tmp_path))
    try:
        setup_logging(verbose=True)
        assert log.level == logging.DEBUG
        setup_logging(verbose=False)
        assert log.level == logging.INFO
    finally:
        for handler in log.handlers:
            handler.close()
        log.handlers.clear()
