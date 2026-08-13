from datetime import datetime, timedelta, timezone

import pytest

import collectors
import pipeline
from dispatchers.telegram import TelegramError
from models import Article
from pipeline import run
from state import State, load_state, save_state

UTC = timezone.utc
NOW = datetime(2026, 8, 13, 8, 0, tzinfo=UTC)


def article(headline, hours_ago=1, source="A", category="C"):
    return Article(
        category=category,
        source=source,
        headline=headline,
        link=f"https://x.test/{headline}",
        published=NOW - timedelta(hours=hours_ago),
        blurb="",
    )


@pytest.fixture
def config():
    return {
        "telegram": {"include_blurb": False, "send_when_empty": False},
        "freshness": {"overlap_hours": 6, "first_run_lookback_hours": 24},
        "limits": {"max_per_source": 10, "max_total": 60},
        "history": {"max_entries": 800},
        "sources": [
            {"name": "A", "category": "C", "type": "feed", "url": "https://a.test"}
        ],
    }


@pytest.fixture
def stub_collect(monkeypatch):
    """Make the 'feed' strategy return whatever the test sets."""
    box = {"articles": [article("one"), article("two")], "error": None}

    def strategy(source, session):
        if box["error"]:
            raise box["error"]
        return box["articles"]

    monkeypatch.setitem(collectors.STRATEGIES, "feed", strategy)
    return box


class Recorder:
    """Stands in for telegram.dispatch."""

    def __init__(self, fail_after=None, error=None):
        self.fail_after = fail_after
        self.error = error
        self.chunks = None

    def __call__(self, chunks):
        self.chunks = chunks
        ids = [i for c in chunks for i in c.article_ids]
        if self.error and self.fail_after is None:
            return [], self.error
        if self.fail_after is not None:
            delivered = [i for c in chunks[: self.fail_after] for i in c.article_ids]
            return delivered, TelegramError("second message failed")
        return ids, None


def test_successful_run_persists_ids_and_last_run(tmp_path, config, stub_collect):
    path = tmp_path / "history.json"
    outcome = run(config, session=None, now=NOW, state_path=path, dispatch_fn=Recorder())
    assert outcome.exit_code == 0
    saved = load_state(path)
    assert saved.last_run == NOW
    assert len(saved.seen) == 2


def test_telegram_failure_leaves_last_run_untouched(tmp_path, config, stub_collect):
    """THE critical invariant. If this regresses, articles vanish."""
    path = tmp_path / "history.json"
    previous = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
    save_state(State(last_run=previous, seen=[]), path)

    outcome = run(
        config,
        session=None,
        now=NOW,
        state_path=path,
        dispatch_fn=Recorder(error=TelegramError("token revoked")),
    )
    assert outcome.exit_code == 2
    saved = load_state(path)
    assert saved.last_run == previous
    assert saved.seen == []


def test_partial_delivery_records_only_delivered_ids(tmp_path, config, stub_collect):
    path = tmp_path / "history.json"
    previous = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
    save_state(State(last_run=previous, seen=[]), path)
    # Raise the limits so all 200 articles survive apply_limits (the default
    # max_per_source=10 would otherwise clip a single-source batch down to
    # 10 items, which fits in one Telegram chunk and never exercises the
    # multi-chunk partial-delivery path this test targets).
    config["limits"]["max_per_source"] = 200
    config["limits"]["max_total"] = 200
    stub_collect["articles"] = [article(f"h{i}") for i in range(200)]

    recorder = Recorder(fail_after=1)
    outcome = run(
        config, session=None, now=NOW, state_path=path, dispatch_fn=recorder
    )
    assert outcome.exit_code == 2
    assert len(recorder.chunks) > 1
    saved = load_state(path)
    assert saved.seen == recorder.chunks[0].article_ids
    assert saved.last_run == previous


def test_already_seen_articles_are_not_resent(tmp_path, config, stub_collect):
    path = tmp_path / "history.json"
    save_state(
        State(last_run=NOW - timedelta(days=1), seen=["https://x.test/one"]), path
    )
    recorder = Recorder()
    run(config, session=None, now=NOW, state_path=path, dispatch_fn=recorder)
    delivered = [i for c in recorder.chunks for i in c.article_ids]
    assert delivered == ["https://x.test/two"]


def test_stale_articles_are_gated_out(tmp_path, config, stub_collect):
    path = tmp_path / "history.json"
    save_state(State(last_run=NOW - timedelta(hours=2), seen=[]), path)
    stub_collect["articles"] = [article("fresh", hours_ago=1), article("old", hours_ago=48)]
    recorder = Recorder()
    run(config, session=None, now=NOW, state_path=path, dispatch_fn=recorder)
    delivered = [i for c in recorder.chunks for i in c.article_ids]
    assert delivered == ["https://x.test/fresh"]


def test_all_sources_failing_exits_3_and_persists_nothing(tmp_path, config, stub_collect):
    path = tmp_path / "history.json"
    stub_collect["error"] = RuntimeError("network down")
    outcome = run(config, session=None, now=NOW, state_path=path, dispatch_fn=Recorder())
    assert outcome.exit_code == 3
    assert not path.exists()


def test_all_sources_disabled_exits_zero_not_three(tmp_path, config):
    """attempted == 0 (nothing configured to run) must not be reported as an outage."""
    path = tmp_path / "history.json"
    config["sources"][0]["enabled"] = False
    outcome = run(config, session=None, now=NOW, state_path=path, dispatch_fn=Recorder())
    assert outcome.exit_code == 0


def test_no_new_articles_sends_nothing_but_advances_last_run(tmp_path, config, stub_collect):
    path = tmp_path / "history.json"
    stub_collect["articles"] = []
    recorder = Recorder()
    outcome = run(config, session=None, now=NOW, state_path=path, dispatch_fn=recorder)
    assert outcome.exit_code == 0
    assert recorder.chunks is None
    assert load_state(path).last_run == NOW


def test_dry_run_persists_nothing(tmp_path, config, stub_collect):
    path = tmp_path / "history.json"
    recorder = Recorder()
    outcome = run(
        config, session=None, now=NOW, state_path=path, dry_run=True, dispatch_fn=recorder
    )
    assert outcome.exit_code == 0
    assert recorder.chunks is None
    assert not path.exists()


def test_dry_run_persists_nothing_when_no_new_articles(tmp_path, config, stub_collect):
    """The empty-digest path has its own persistence branch; dry-run must
    short-circuit it too, not just the normal-digest path."""
    path = tmp_path / "history.json"
    stub_collect["articles"] = []
    recorder = Recorder()
    outcome = run(
        config, session=None, now=NOW, state_path=path, dry_run=True, dispatch_fn=recorder
    )
    assert outcome.exit_code == 0
    assert recorder.chunks is None
    assert not path.exists()


def test_init_seeds_everything_without_sending(tmp_path, config, stub_collect):
    path = tmp_path / "history.json"
    config["limits"]["max_total"] = 1
    stub_collect["articles"] = [article(f"h{i}") for i in range(5)]
    recorder = Recorder()
    outcome = run(
        config, session=None, now=NOW, state_path=path, init=True, dispatch_fn=recorder
    )
    assert outcome.exit_code == 0
    assert recorder.chunks is None
    saved = load_state(path)
    assert len(saved.seen) == 5
    assert saved.last_run == NOW


def test_init_respects_dry_run(tmp_path, config, stub_collect):
    """--init --dry-run must persist nothing, matching rule 4 exactly like
    the empty-digest path already does. Regression for a defect where the
    init branch called save_state unconditionally."""
    path = tmp_path / "history.json"
    recorder = Recorder()
    outcome = run(
        config,
        session=None,
        now=NOW,
        state_path=path,
        init=True,
        dry_run=True,
        dispatch_fn=recorder,
    )
    assert outcome.exit_code == 0
    assert recorder.chunks is None
    assert not path.exists()


def test_stage_order_is_dedup_gate_then_limits(tmp_path, config, stub_collect):
    """Pin the fixed stage order: dedup, then gate, then limits.

    If limits ran before dedup, the per-source cap (max_per_source=1 here)
    would spend its one slot on the already-seen, newer article and drop
    the fresh one -- silently losing it instead of the seen one dedup
    should have removed. Both articles are well within the freshness
    cutoff, so this isolates the dedup/limits ordering specifically.
    """
    path = tmp_path / "history.json"
    save_state(
        State(last_run=NOW - timedelta(hours=1), seen=["https://x.test/seen"]), path
    )
    config["limits"]["max_per_source"] = 1
    stub_collect["articles"] = [
        article("seen", hours_ago=1),  # newest, but already delivered
        article("new", hours_ago=2),  # older, but never delivered
    ]
    recorder = Recorder()
    run(config, session=None, now=NOW, state_path=path, dispatch_fn=recorder)
    assert recorder.chunks is not None, "dedup must run before limits"
    delivered = [i for c in recorder.chunks for i in c.article_ids]
    assert delivered == ["https://x.test/new"]


def test_extras_run_only_after_successful_delivery(tmp_path, config, stub_collect):
    path = tmp_path / "history.json"
    calls = []
    run(
        config,
        session=None,
        now=NOW,
        state_path=path,
        dispatch_fn=Recorder(),
        extras_fn=lambda articles: calls.append(len(articles)),
    )
    assert calls == [2]

    calls.clear()
    run(
        config,
        session=None,
        now=NOW,
        state_path=tmp_path / "h2.json",
        dispatch_fn=Recorder(error=TelegramError("nope")),
        extras_fn=lambda articles: calls.append(len(articles)),
    )
    assert calls == []


def test_extras_failure_does_not_change_exit_code(tmp_path, config, stub_collect):
    def boom(articles):
        raise RuntimeError("ffmpeg exploded")

    outcome = run(
        config,
        session=None,
        now=NOW,
        state_path=tmp_path / "history.json",
        dispatch_fn=Recorder(),
        extras_fn=boom,
    )
    assert outcome.exit_code == 0


def test_default_dispatch_forwards_token_chat_and_preview_flag(monkeypatch):
    """No test at all exercised _default_dispatch before this: a swapped
    token/chat_id argument, or a dropped disable_preview flag, would have
    sailed through review unnoticed. Stub telegram.dispatch and check what
    it actually receives."""
    monkeypatch.setenv("TECHNEWS_TELEGRAM_BOT_TOKEN", "tok-123")
    monkeypatch.setenv("TECHNEWS_TELEGRAM_CHAT_ID", "chat-456")

    calls = []

    def fake_dispatch(session, token, chat_id, chunks, *, disable_preview=True):
        calls.append((session, token, chat_id, chunks, disable_preview))
        return (["delivered-id"], None)

    monkeypatch.setattr(pipeline.telegram, "dispatch", fake_dispatch)

    session = object()
    config = {"telegram": {"disable_web_page_preview": False}}
    dispatch_fn = pipeline._default_dispatch(session, config)
    result = dispatch_fn(["chunk-a", "chunk-b"])

    assert calls == [(session, "tok-123", "chat-456", ["chunk-a", "chunk-b"], False)]
    assert result == (["delivered-id"], None)
