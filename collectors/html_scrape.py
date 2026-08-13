"""CSS-selector scraping for sources that publish no feed.

Dates are only trusted when machine-readable: a <time datetime="..."> or
similar ISO 8601 attribute. Human date text is deliberately not guessed at
— an article with no reliable date gets published=None and is handled by
the freshness gate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from collectors.http import fetch
from models import Article, log

BLURB_LIMIT = 200


def parse_time_attribute(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _select_text(node, selector: str | None) -> str:
    if not selector:
        return ""
    found = node.select_one(selector)
    return found.get_text(strip=True) if found else ""


def collect(source: dict, session) -> list[Article]:
    selectors = source.get("selectors") or {}
    for required in ("item", "title", "link"):
        if not selectors.get(required):
            raise ValueError(
                f"Source {source['name']} is missing selectors.{required}"
            )

    raw = fetch(session, source["url"])
    soup = BeautifulSoup(raw, "lxml")
    items = soup.select(selectors["item"])
    if not items:
        raise ValueError(
            f"Source {source['name']} matched no items for "
            f"selector {selectors['item']!r}"
        )

    articles: list[Article] = []
    skipped = 0
    for item in items:
        headline = _select_text(item, selectors["title"])
        anchor = item.select_one(selectors["link"])
        href = anchor.get("href") if anchor else None
        if not headline or not href:
            skipped += 1
            log.warning("Skipping item without title or link in %s", source["name"])
            continue

        published = None
        if selectors.get("date"):
            date_node = item.select_one(selectors["date"])
            if date_node is not None:
                published = parse_time_attribute(
                    date_node.get("datetime") or date_node.get("content") or ""
                )

        articles.append(
            Article(
                category=source["category"],
                source=source["name"],
                headline=headline,
                link=urljoin(source["url"], href),
                published=published,
                blurb=_select_text(item, selectors.get("blurb"))[:BLURB_LIMIT],
            )
        )

    if not articles and skipped:
        raise ValueError(
            f"Source {source['name']} matched {skipped} item(s) with selector "
            f"{selectors['item']!r} but built 0 articles from them (title or "
            "link selector likely broken)"
        )

    return articles
