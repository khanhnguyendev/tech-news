import json
from pathlib import Path

import pytest

import main as main_module
from main import build_parser, main

CONFIG = """
sources:
  - name: "A"
    category: "C"
    type: feed
    url: "https://a.test/rss"
"""


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG)
    return path


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TECHNEWS_DATA_DIR", str(tmp_path / "data"))


def test_parser_defaults():
    args = build_parser().parse_args([])
    assert args.dry_run is False
    assert args.init is False
    assert args.reset is False
    assert args.only is None


def test_parser_accepts_all_flags(tmp_path):
    args = build_parser().parse_args(
        ["--dry-run", "--only", "Krebs", "--config", str(tmp_path / "c.yaml")]
    )
    assert args.dry_run is True
    assert args.only == "Krebs"


def test_missing_config_exits_1(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "absent.yaml")]) == 1
    assert "not found" in capsys.readouterr().err.lower()


def test_invalid_config_exits_1(tmp_path, capsys):
    bad = tmp_path / "config.yaml"
    bad.write_text("sources:\n  - name: X\n    category: C\n    type: bogus\n")
    assert main(["--config", str(bad)]) == 1


def test_exit_code_comes_from_pipeline(config_file, monkeypatch):
    from pipeline import RunOutcome

    monkeypatch.setenv("TECHNEWS_TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TECHNEWS_TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setattr(
        main_module.pipeline, "run", lambda *a, **k: RunOutcome(2, [], [])
    )
    assert main(["--config", str(config_file)]) == 2


def test_reset_deletes_history_and_exits_0(config_file, tmp_path, monkeypatch):
    history = Path(tmp_path / "data") / "history.json"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text(json.dumps({"version": 1, "seen": [], "last_run": None}))
    assert main(["--config", str(config_file), "--reset", "--yes"]) == 0
    assert not history.exists()


def test_reset_tells_the_user_to_init_before_the_next_real_run(
    config_file, tmp_path, capsys
):
    """A bare --reset followed by an ordinary run would flood the chat with
    every currently visible item (bounded only by the normal limits). The
    reminder is the only guard against that happening by accident."""
    history = Path(tmp_path / "data") / "history.json"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text(json.dumps({"version": 1, "seen": [], "last_run": None}))
    main(["--config", str(config_file), "--reset", "--yes"])
    assert "--init" in capsys.readouterr().out


def test_reset_without_confirmation_prints_no_init_reminder(
    config_file, tmp_path, monkeypatch, capsys
):
    """Cancelling --reset leaves history untouched, so there is nothing to
    re-seed and the reminder must not appear."""
    history = Path(tmp_path / "data") / "history.json"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text("{}")
    monkeypatch.setattr("builtins.input", lambda _: "n")
    main(["--config", str(config_file), "--reset"])
    assert "--init" not in capsys.readouterr().out


def test_reset_without_yes_asks_for_confirmation(config_file, tmp_path, monkeypatch):
    history = Path(tmp_path / "data") / "history.json"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text("{}")
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert main(["--config", str(config_file), "--reset"]) == 0
    assert history.exists()


def test_reset_on_missing_history_is_not_an_error(config_file):
    assert main(["--config", str(config_file), "--reset", "--yes"]) == 0


def test_dry_run_needs_no_telegram_secrets(config_file, monkeypatch):
    """A dry run must work before the user has a bot token."""
    monkeypatch.delenv("TECHNEWS_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TECHNEWS_TELEGRAM_CHAT_ID", raising=False)
    import collectors

    monkeypatch.setitem(collectors.STRATEGIES, "feed", lambda s, sess: [])
    assert main(["--config", str(config_file), "--dry-run"]) == 0
