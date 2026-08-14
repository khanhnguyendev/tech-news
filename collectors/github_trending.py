"""GitHub Trending collection by scraping the trending page directly.

The original design took trending from a community RSS mirror. That mirror
carries the repo name and its README, but no star counts -- and "how many
stars did this gain today" is the only number that actually says whether a
listing is notable. The trending page itself carries the description, the
language, the total, and today's gain in one request, so scraping it is
both richer and one dependency lighter. The spec's risk register already
named this as the mirror's replacement.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from collectors.http import fetch
from models import Article, log

TRENDING_URL = "https://github.com/trending"
DESCRIPTION_LIMIT = 200

_STARS_TODAY_RE = re.compile(r"([\d,]+)\s*stars?\s*today", re.I)


def format_stars(count: int) -> str:
    """Compact star count: 999, 1.0k, 15.7k, 169k, 1.2M.

    Trending totals span three orders of magnitude and sit next to the
    number that matters more (today's gain), so they are abbreviated to
    keep the line short enough to read at a glance.
    """
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 100_000:
        return f"{count / 1000:.0f}k"
    if count >= 1000:
        return f"{count / 1000:.1f}k"
    return str(count)


def _digits(text: str | None) -> int | None:
    if not text:
        return None
    found = re.search(r"[\d,]+", text)
    return int(found.group(0).replace(",", "")) if found else None


def _repo_name(row) -> str | None:
    anchor = row.select_one("h2 a")
    if anchor is None:
        return None
    href = (anchor.get("href") or "").strip("/")
    if href:
        return href
    # Fall back to the visible text, which GitHub renders as "owner / repo"
    # across nested elements.
    return "".join(anchor.get_text().split())


def _text(node) -> str:
    return " ".join(node.get_text().split()) if node is not None else ""


def collect(source: dict, session) -> list[Article]:
    url = source.get("url") or TRENDING_URL
    soup = BeautifulSoup(fetch(session, url), "lxml")
    rows = soup.select("article.Box-row")
    if not rows:
        raise ValueError(
            f"Source {source['name']} matched no repositories at {url}; "
            "GitHub's trending markup has probably changed"
        )

    articles: list[Article] = []
    skipped = 0
    for row in rows:
        name = _repo_name(row)
        if not name:
            skipped += 1
            log.warning("Skipping a trending row with no repo name in %s", source["name"])
            continue

        total = _digits(_text(row.select_one('a[href$="/stargazers"]')))
        # Matched rather than digit-scraped: the row holds several numbers,
        # and only the one followed by "stars today" is the daily gain.
        gain = _STARS_TODAY_RE.search(_text(row.select_one("span.float-sm-right")))
        today = int(gain.group(1).replace(",", "")) if gain else None
        language = _text(row.select_one("[itemprop=programmingLanguage]"))
        description = _text(row.select_one("p"))[:DESCRIPTION_LIMIT]

        # Only the parts that exist, so a repo with no language does not
        # leave a dangling separator behind.
        metrics = [f"⭐ {format_stars(total)}"] if total is not None else []
        if today is not None:
            metrics.append(f"+{today:,} today")
        if language:
            metrics.append(language)

        blurb = " · ".join(metrics)
        if description:
            blurb = f"{blurb}\n{description}" if blurb else description

        articles.append(
            Article(
                category=source["category"],
                source=source["name"],
                headline=name,
                link=f"https://github.com/{name}",
                published=None,
                blurb=blurb,
            )
        )

    if not articles and skipped:
        raise ValueError(
            f"Source {source['name']} matched {skipped} row(s) but built no "
            "articles; GitHub's trending markup has probably changed"
        )
    return articles
