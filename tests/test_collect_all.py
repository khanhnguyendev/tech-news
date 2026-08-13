from datetime import datetime, timezone

import pytest

import collectors
from collectors import CollectResult, collect_all, matches_keywords
from models import Article

UTC = timezone.utc


def article(headline="H", source="S", blurb=""):
    return Article("C", source, headline, f"https://x.test/{headline}", None, blurb)


@pytest.fixture
def stub_strategies(monkeypatch):
    """Replace the real strategies with controllable stubs."""
    calls = []

    def good(source, session):
        calls.append(source["name"])
        return [article(headline=f"{source['name']}-1", source=source["name"])]

    def bad(source, session):
        calls.append(source["name"])
        raise RuntimeError("upstream exploded")

    monkeypatch.setitem(collectors.STRATEGIES, "feed", good)
    monkeypatch.setitem(collectors.STRATEGIES, "html", bad)
    return calls


def src(name, type_="feed", **extra):
    base = {"name": name, "category": "C", "type": type_, "url": "https://x.test"}
    base.update(extra)
    return base


def test_collects_from_every_enabled_source(stub_strategies):
    result = collect_all([src("A"), src("B")], session=None)
    assert len(result.articles) == 2
    assert result.ok_count == 2
    assert result.failed_count == 0


def test_one_failing_source_does_not_stop_the_others(stub_strategies, caplog):
    with caplog.at_level("ERROR", logger="technews"):
        result = collect_all([src("A"), src("Boom", "html"), src("B")], session=None)
    assert [a.source for a in result.articles] == ["A", "B"]
    assert result.failed_count == 1
    assert "Boom" in caplog.text


def test_disabled_sources_are_skipped(stub_strategies):
    result = collect_all([src("A"), src("Off", enabled=False)], session=None)
    assert result.skipped_count == 1
    assert "Off" not in stub_strategies


def test_only_filter_runs_a_single_source(stub_strategies):
    result = collect_all([src("A"), src("B")], session=None, only="B")
    assert [a.source for a in result.articles] == ["B"]


def test_unknown_strategy_type_is_isolated_not_raised(stub_strategies, caplog):
    """load_config() rejects an unknown `type` today, so this path is
    unreachable through the normal CLI -- but the isolation boundary
    (collect_all wraps each source in its own try/except so one broken
    source can't take the whole run down) is supposed to cover every
    source-level failure, including a STRATEGIES lookup miss, not just
    the strategy call itself. A config built directly (bypassing
    load_config's validation, as a test or a future caller might) must
    still get isolated instead of raising a bare KeyError past the
    boundary."""
    with caplog.at_level("ERROR", logger="technews"):
        result = collect_all(
            [src("A"), src("Bogus", type_="carrier_pigeon"), src("B")], session=None
        )
    assert [a.source for a in result.articles] == ["A", "B"]
    assert result.failed_count == 1
    assert result.ok_count == 2
    assert "Bogus" in caplog.text


def test_only_filter_with_unknown_name_raises(stub_strategies):
    with pytest.raises(ValueError, match="No source named"):
        collect_all([src("A")], session=None, only="Nope")


def test_keywords_filter_articles(stub_strategies, monkeypatch):
    def many(source, session):
        return [
            article(headline="Rust memory safety", source=source["name"]),
            article(headline="Cooking pasta", source=source["name"]),
        ]

    monkeypatch.setitem(collectors.STRATEGIES, "feed", many)
    result = collect_all([src("A", keywords=["rust"])], session=None)
    assert [a.headline for a in result.articles] == ["Rust memory safety"]


def test_matches_keywords_is_case_insensitive_across_title_and_blurb():
    assert matches_keywords(article(headline="RUST is fast"), ["rust"])
    assert matches_keywords(article(blurb="written in Rust"), ["rust"])
    assert not matches_keywords(article(headline="Go is fast"), ["rust"])


def test_empty_keywords_means_no_filtering():
    assert matches_keywords(article(headline="anything"), [])
