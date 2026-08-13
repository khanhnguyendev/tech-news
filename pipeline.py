"""Pipeline stages. Everything here is pure and independently testable."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from models import Article, log

_FAR_PAST = datetime.min.replace(tzinfo=timezone.utc)


def sort_articles(articles: list[Article]) -> list[Article]:
    """Newest first, undated last, ties broken deterministically."""
    return sorted(
        articles,
        key=lambda a: (
            a.published is None,
            -(a.published or _FAR_PAST).timestamp(),
            a.source,
            a.headline,
        ),
    )


def drop_seen(articles: list[Article], seen: set[str]) -> list[Article]:
    kept = [a for a in articles if a.id not in seen]
    dropped = len(articles) - len(kept)
    if dropped:
        log.info("Dedup: dropped %d already-reported item(s)", dropped)
    return kept


def apply_gate(
    articles: list[Article], cutoff: datetime, gates: dict[str, str]
) -> list[Article]:
    """Drop stale items. Sources gated 'new_only' skip the time check."""
    kept: list[Article] = []
    for article in articles:
        if gates.get(article.source, "published") == "new_only":
            kept.append(article)
        elif article.published is not None and article.published >= cutoff:
            kept.append(article)
    dropped = len(articles) - len(kept)
    if dropped:
        log.info("Freshness gate: dropped %d stale or undated item(s)", dropped)
    return kept


def apply_limits(
    articles: list[Article], max_per_source: int, max_total: int
) -> list[Article]:
    """Cap per source, then overall. Never truncate silently."""
    ordered = sort_articles(articles)

    per_source: dict[str, int] = defaultdict(int)
    kept: list[Article] = []
    dropped_by_source: dict[str, int] = defaultdict(int)
    for article in ordered:
        if per_source[article.source] < max_per_source:
            per_source[article.source] += 1
            kept.append(article)
        else:
            dropped_by_source[article.source] += 1

    for source, count in sorted(dropped_by_source.items()):
        log.info("Per-source limit for %s: dropped %d item(s)", source, count)

    if len(kept) > max_total:
        log.info("Total limit: dropped %d item(s)", len(kept) - max_total)
        kept = kept[:max_total]

    return kept
