"""GitHub release collection via the REST API.

Unauthenticated access allows 60 requests per hour and this pipeline uses
about three per day, so GITHUB_TOKEN is optional.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from collectors.http import fetch_json
from models import Article, log, truncate

API_TEMPLATE = "https://api.github.com/repos/{repo}/releases"
BLURB_LIMIT = 200


def _headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def collect(source: dict, session) -> list[Article]:
    include_prereleases = bool(source.get("include_prereleases", False))
    url = API_TEMPLATE.format(repo=source["repo"])
    releases = fetch_json(session, url, headers=_headers())

    articles: list[Article] = []
    for release in releases:
        if release.get("draft"):
            continue
        if release.get("prerelease") and not include_prereleases:
            continue
        headline = (release.get("name") or "").strip() or release.get("tag_name")
        link = release.get("html_url")
        if not headline or not link:
            log.warning("Skipping malformed release entry in %s", source["name"])
            continue
        articles.append(
            Article(
                category=source["category"],
                source=source["name"],
                headline=headline,
                link=link,
                published=_parse_timestamp(release.get("published_at")),
                blurb=truncate((release.get("body") or "").strip(), BLURB_LIMIT),
            )
        )
    return articles
