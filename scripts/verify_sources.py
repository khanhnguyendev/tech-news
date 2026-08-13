#!/usr/bin/env python3
"""Fetch every configured source once and report what works.

Run by hand, never from a test. Also saves each response under
tests/fixtures/live/ so real payloads are available when a collector
misbehaves later.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collectors import STRATEGIES  # noqa: E402
from collectors.github_release import API_TEMPLATE  # noqa: E402
from collectors.http import fetch, fetch_json, make_session  # noqa: E402
from settings import load_config  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
LIVE_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "live"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    return _SLUG_RE.sub("_", name.lower()).strip("_")


def _save_fixture(source: dict, session) -> None:
    """Best-effort: save the raw response for a source under tests/fixtures/live/.

    Failures here must never affect the pass/fail verdict for a source --
    fixture capture is a bonus of running this script, not its purpose.
    """
    source_type = source["type"]
    slug = _slug(source["name"])
    try:
        if source_type in ("feed", "html"):
            ext = "xml" if source_type == "feed" else "html"
            raw = fetch(session, source["url"])
            LIVE_FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
            (LIVE_FIXTURES_DIR / f"{slug}.{ext}").write_bytes(raw)
        elif source_type == "github_release":
            url = API_TEMPLATE.format(repo=source["repo"])
            data = fetch_json(session, url, headers={"Accept": "application/vnd.github+json"})
            LIVE_FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
            (LIVE_FIXTURES_DIR / f"{slug}.json").write_text(
                json.dumps(data, indent=2), encoding="utf-8"
            )
    except Exception as exc:  # noqa: BLE001 - fixture capture is best-effort
        print(f"      (fixture capture skipped: {type(exc).__name__}: {exc})")


def main() -> int:
    config = load_config(CONFIG_PATH)
    session = make_session()
    failures = 0

    for source in config["sources"]:
        name = source["name"]
        if not source.get("enabled", True):
            print(f"SKIP  {name}")
            continue
        try:
            articles = STRATEGIES[source["type"]](source, session)
        except Exception as exc:
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
            failures += 1
            continue

        _save_fixture(source, session)

        dated = sum(1 for a in articles if a.published is not None)
        status = "OK  " if articles else "EMPTY"
        newest = max(
            (a.published for a in articles if a.published), default=None
        )
        print(
            f"{status}  {name}: {len(articles)} item(s), "
            f"{dated} dated, newest={newest}"
        )
        if not articles or (dated == 0 and source.get("gate") != "new_only"):
            failures += 1

    print(f"\n{failures} source(s) need attention")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
