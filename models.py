"""Core data model, URL normalization, filesystem paths, and logging."""

from __future__ import annotations

import logging
import logging.handlers
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DATA_DIR = Path(os.environ.get("TECHNEWS_DATA_DIR", Path.home() / ".technews"))
HISTORY_FILE = DATA_DIR / "history.json"
LOG_FILE = DATA_DIR / "app.log"

USER_AGENT = "TechNews/1.0 (+https://github.com/khanhnguyendev/tech-news)"
HTTP_TIMEOUT = 20

TRACKING_PARAMS = ("fbclid", "gclid", "mc_cid", "mc_eid")

log = logging.getLogger("technews")


def normalize_url(url: str) -> str:
    """Return the canonical form of a URL, used as the dedup identity.

    Drops the fragment and tracking parameters, strips one trailing slash,
    and lowercases scheme and host. Path case is preserved because many
    sites treat paths case-sensitively.
    """
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), "")
    )


@dataclass(frozen=True, slots=True)
class Article:
    category: str
    source: str
    headline: str
    link: str
    published: datetime | None
    blurb: str = ""

    @property
    def id(self) -> str:
        return normalize_url(self.link)


def setup_logging(verbose: bool = False) -> None:
    """Configure a rotating file log plus stderr output."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    log.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    log.addHandler(stream_handler)
