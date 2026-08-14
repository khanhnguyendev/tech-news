"""Core data model, URL normalization, filesystem paths, and logging."""

from __future__ import annotations

import logging
import logging.handlers
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def data_dir() -> Path:
    """The data directory, resolved from TECHNEWS_DATA_DIR right now.

    Call this (or history_file()/log_file()) instead of the DATA_DIR/
    HISTORY_FILE/LOG_FILE constants below wherever the current environment
    matters -- e.g. once per CLI invocation, or once per test. The module
    constants are computed a single time, when this module is first
    imported, and never change afterwards; that is fine for a short-lived
    process but wrong inside a long-lived one (such as a test session)
    where different callers want different data directories.
    """
    return Path(os.environ.get("TECHNEWS_DATA_DIR", str(Path.home() / ".technews")))


def history_file() -> Path:
    return data_dir() / "history.json"


def log_file() -> Path:
    return data_dir() / "app.log"


# Bound once, at import time, from whatever TECHNEWS_DATA_DIR is set to at
# that moment. Kept for backward compatibility with code that imports these
# names directly (state.py's and pipeline.py's default arguments, in
# particular). Prefer the data_dir()/history_file()/log_file() functions
# above in anything that resolves the path at call time.
DATA_DIR = data_dir()
HISTORY_FILE = history_file()
LOG_FILE = log_file()

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


ELLIPSIS = "\u2026"


def truncate(text: str, limit: int) -> str:
    """Shorten text to `limit` characters, cutting at a word boundary.

    A blind `text[:limit]` slices mid-word, which in a digest reads as a
    typo rather than as an abbreviation -- "Zero cost, zero conf" looks
    broken, not shortened. The ellipsis counts toward the limit, so the
    result never exceeds it, and it is only added when something really
    was cut.
    """
    if len(text) <= limit:
        return text
    window = text[: limit - len(ELLIPSIS)]
    cut = window.rsplit(" ", 1)[0] if " " in window else window
    return cut.rstrip(" ,.;:-") + ELLIPSIS


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
    current_data_dir = data_dir()
    current_data_dir.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        log_file(), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    log.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    log.addHandler(stream_handler)
