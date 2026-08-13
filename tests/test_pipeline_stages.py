from datetime import datetime, timezone

from models import Article
from pipeline import apply_gate, apply_limits, drop_seen, sort_articles

UTC = timezone.utc


def make(headline, published, source="S", category="C", link=None):
    return Article(
        category=category,
        source=source,
        headline=headline,
        link=link or f"https://x.test/{headline}",
        published=published,
    )


def test_sort_is_newest_first_with_none_last():
    a = make("old", datetime(2026, 8, 10, tzinfo=UTC))
    b = make("new", datetime(2026, 8, 12, tzinfo=UTC))
    c = make("undated", None)
    assert [x.headline for x in sort_articles([a, c, b])] == ["new", "old", "undated"]


def test_sort_breaks_ties_deterministically():
    stamp = datetime(2026, 8, 12, tzinfo=UTC)
    a = make("zeta", stamp, source="B")
    b = make("alpha", stamp, source="A")
    assert [x.source for x in sort_articles([a, b])] == ["A", "B"]


def test_drop_seen_removes_known_ids():
    keep = make("keep", None, link="https://x.test/keep")
    drop = make("drop", None, link="https://x.test/drop/?utm_source=rss")
    result = drop_seen([keep, drop], {"https://x.test/drop"})
    assert [a.headline for a in result] == ["keep"]


def test_gate_keeps_items_at_or_after_cutoff():
    cutoff = datetime(2026, 8, 13, 2, 0, tzinfo=UTC)
    exactly = make("exactly", cutoff)
    after = make("after", datetime(2026, 8, 13, 3, 0, tzinfo=UTC))
    before = make("before", datetime(2026, 8, 13, 1, 0, tzinfo=UTC))
    result = apply_gate([exactly, after, before], cutoff, {"S": "published"})
    assert sorted(a.headline for a in result) == ["after", "exactly"]


def test_gate_drops_undated_articles():
    cutoff = datetime(2026, 8, 13, tzinfo=UTC)
    assert apply_gate([make("x", None)], cutoff, {"S": "published"}) == []


def test_gate_new_only_bypasses_time_check():
    cutoff = datetime(2026, 8, 13, tzinfo=UTC)
    ancient = make("ancient", datetime(2020, 1, 1, tzinfo=UTC), source="Events")
    undated = make("undated", None, source="Events")
    result = apply_gate([ancient, undated], cutoff, {"Events": "new_only"})
    assert len(result) == 2


def test_limits_cap_per_source_keeping_newest():
    items = [
        make(f"h{i}", datetime(2026, 8, i + 1, tzinfo=UTC), source="A") for i in range(5)
    ]
    result = apply_limits(items, max_per_source=2, max_total=99)
    assert [a.headline for a in result] == ["h4", "h3"]


def test_limits_cap_total_across_sources():
    items = [
        make("a1", datetime(2026, 8, 5, tzinfo=UTC), source="A"),
        make("b1", datetime(2026, 8, 4, tzinfo=UTC), source="B"),
        make("c1", datetime(2026, 8, 3, tzinfo=UTC), source="C"),
    ]
    assert len(apply_limits(items, max_per_source=10, max_total=2)) == 2


def test_limits_log_dropped_counts(caplog):
    items = [
        make(f"h{i}", datetime(2026, 8, i + 1, tzinfo=UTC), source="A") for i in range(5)
    ]
    with caplog.at_level("INFO", logger="technews"):
        apply_limits(items, max_per_source=2, max_total=99)
    assert "dropped 3" in caplog.text.lower()
