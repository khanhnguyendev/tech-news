"""Pipeline stages, plus run(), the orchestrator. The stages above run()
are pure and independently testable; run() itself does file I/O and
network dispatch."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from collectors import collect_all
from dispatchers import telegram
from models import Article, HISTORY_FILE, log
from settings import category_order, gate_by_source, get_secret
from state import freshness_cutoff, load_state, save_state

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


@dataclass
class RunOutcome:
    exit_code: int
    articles: list[Article]
    delivered_ids: list[str]


def _default_dispatch(session, config):
    token = get_secret("TECHNEWS_TELEGRAM_BOT_TOKEN")
    chat_id = get_secret("TECHNEWS_TELEGRAM_CHAT_ID")
    disable_preview = bool(
        config.get("telegram", {}).get("disable_web_page_preview", True)
    )

    def dispatch_fn(chunks):
        return telegram.dispatch(
            session, token, chat_id, chunks, disable_preview=disable_preview
        )

    return dispatch_fn


def run(
    config: dict,
    *,
    session,
    now: datetime,
    dry_run: bool = False,
    init: bool = False,
    only: str | None = None,
    state_path: Path = HISTORY_FILE,
    dispatch_fn=None,
    extras_fn=None,
) -> RunOutcome:
    """Execute one full pipeline run and return its outcome.

    `now` is the moment the run started; it becomes last_run on success.
    Taking the start time rather than the finish time closes the window in
    which an article published mid-run would fall between two runs.
    """
    telegram_cfg = config.get("telegram", {})
    freshness_cfg = config.get("freshness", {})
    limits_cfg = config.get("limits", {})
    history_cfg = config.get("history", {})

    state = load_state(state_path)
    max_entries = int(history_cfg.get("max_entries", 800))

    def persist() -> None:
        save_state(state, state_path, max_entries)

    cutoff = freshness_cutoff(
        state,
        now,
        int(freshness_cfg.get("overlap_hours", 6)),
        int(freshness_cfg.get("first_run_lookback_hours", 24)),
    )
    log.info("Freshness cutoff: %s", cutoff.isoformat())

    collected = collect_all(config["sources"], session, only=only)
    if collected.attempted > 0 and collected.ok_count == 0:
        log.error(
            "All %d attempted source(s) failed; nothing persisted", collected.attempted
        )
        return RunOutcome(exit_code=3, articles=[], delivered_ids=[])

    if init:
        if dry_run:
            log.info(
                "Dry run: would seed history with %d id(s); nothing persisted",
                len(collected.articles),
            )
            return RunOutcome(exit_code=0, articles=[], delivered_ids=[])
        state.seen.extend(a.id for a in collected.articles)
        state.last_run = now
        persist()
        log.info("Initialized history with %d id(s)", len(collected.articles))
        return RunOutcome(exit_code=0, articles=[], delivered_ids=[])

    articles = drop_seen(collected.articles, set(state.seen))
    articles = apply_gate(articles, cutoff, gate_by_source(config))
    articles = apply_limits(
        articles,
        int(limits_cfg.get("max_per_source", 10)),
        int(limits_cfg.get("max_total", 60)),
    )
    log.info("%d article(s) ready to dispatch", len(articles))

    if not articles and not telegram_cfg.get("send_when_empty", False):
        log.info("Nothing new today; sending nothing")
        if not dry_run:
            state.last_run = now
            persist()
        return RunOutcome(exit_code=0, articles=[], delivered_ids=[])

    chunks = telegram.render_digest(
        articles,
        category_order(config),
        day=now.date(),
        include_blurb=bool(telegram_cfg.get("include_blurb", False)),
    )

    if dry_run:
        for chunk in chunks:
            print(chunk.html)
            print("-" * 60)
        log.info("Dry run: nothing sent, nothing persisted")
        return RunOutcome(exit_code=0, articles=articles, delivered_ids=[])

    if dispatch_fn is None:
        dispatch_fn = _default_dispatch(session, config)
    delivered, error = dispatch_fn(chunks)

    state.seen.extend(delivered)
    if error is None:
        state.last_run = now
    else:
        log.error("Telegram delivery incomplete; last_run left at %s", state.last_run)
    persist()

    if error is not None:
        return RunOutcome(exit_code=2, articles=articles, delivered_ids=delivered)

    if extras_fn is not None:
        try:
            extras_fn(articles)
        except Exception as exc:  # noqa: BLE001 - extras are never critical
            log.error("Optional output failed: %s: %s", type(exc).__name__, exc)

    return RunOutcome(exit_code=0, articles=articles, delivered_ids=delivered)
