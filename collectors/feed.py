"""RSS and Atom collection.

feedparser does the parsing but never the fetching: bytes come from the
shared session so user agent, timeout, and retry stay in one place.
"""

from __future__ import annotations

import calendar
import html as html_module
import re
from datetime import datetime, timezone

import feedparser

from collectors.http import fetch
from models import Article, log

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,!?;:)\]])")
_WHITESPACE_RE = re.compile(r"\s+")


def strip_html(value: str, limit: int = 200) -> str:
    """Turn a feed summary into a short plain-text blurb."""
    text = _TAG_RE.sub(" ", value or "")
    text = html_module.unescape(text)
    text = _WHITESPACE_RE.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text).strip()
    return text[:limit]


def _to_utc(struct_time) -> datetime | None:
    """feedparser normalizes parsed dates to UTC struct_time."""
    if not struct_time:
        return None
    return datetime.fromtimestamp(calendar.timegm(struct_time), tz=timezone.utc)


def collect(source: dict, session) -> list[Article]:
    raw = fetch(session, source["url"])
    parsed = feedparser.parse(raw)

    if parsed.bozo and parsed.entries:
        log.warning(
            "Feed %s reported a parse problem but yielded entries: %s",
            source["name"],
            parsed.bozo_exception,
        )
    if not parsed.entries:
        raise ValueError(
            f"Feed {source['name']} yielded no entries "
            f"({getattr(parsed, 'bozo_exception', 'no reason given')})"
        )

    articles: list[Article] = []
    for entry in parsed.entries:
        link = entry.get("link")
        title = entry.get("title")
        if not link or not title:
            log.warning("Skipping entry without link or title in %s", source["name"])
            continue
        published = _to_utc(
            entry.get("published_parsed") or entry.get("updated_parsed")
        )
        articles.append(
            Article(
                category=source["category"],
                source=source["name"],
                headline=title.strip(),
                link=link.strip(),
                published=published,
                blurb=strip_html(entry.get("summary", "")),
            )
        )
    return articles
