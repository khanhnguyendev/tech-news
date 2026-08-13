"""Strategy registry and the isolation boundary around collection.

Every source runs inside its own try/except: one dead feed must never take
down the rest of the run. This is the only place that rule is enforced.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from collectors import feed, github_release, html_scrape
from models import Article, log

STRATEGIES = {
    "feed": feed.collect,
    "github_release": github_release.collect,
    "html": html_scrape.collect,
}


@dataclass
class CollectResult:
    articles: list[Article] = field(default_factory=list)
    ok_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0

    @property
    def attempted(self) -> int:
        return self.ok_count + self.failed_count


def matches_keywords(article: Article, keywords: list[str]) -> bool:
    """Case-insensitive substring match over headline and blurb.

    An empty keyword list means the source is not filtered at all.
    """
    if not keywords:
        return True
    haystack = f"{article.headline}\n{article.blurb}".lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def collect_all(sources: list[dict], session, only: str | None = None) -> CollectResult:
    if only is not None:
        names = [s["name"] for s in sources]
        if only not in names:
            raise ValueError(f"No source named {only!r}. Known sources: {names}")
        sources = [s for s in sources if s["name"] == only]

    result = CollectResult()
    for source in sources:
        name = source["name"]
        if not source.get("enabled", True):
            log.info("Skipping disabled source: %s", name)
            result.skipped_count += 1
            continue

        try:
            strategy = STRATEGIES[source["type"]]
            articles = strategy(source, session)
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            log.error("Source %s failed: %s: %s", name, type(exc).__name__, exc)
            result.failed_count += 1
            continue

        keywords = source.get("keywords") or []
        kept = [a for a in articles if matches_keywords(a, keywords)]
        if keywords and len(kept) != len(articles):
            log.info(
                "Keyword filter on %s: kept %d of %d",
                name,
                len(kept),
                len(articles),
            )

        log.info("Source %s: collected %d item(s)", name, len(kept))
        result.articles.extend(kept)
        result.ok_count += 1

    return result
