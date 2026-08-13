import os

import pytest

from settings import (
    ConfigError,
    category_order,
    gate_by_source,
    get_secret,
    load_config,
    load_env,
)

CONFIG = """
telegram:
  include_blurb: false
sources:
  - name: "Anthropic News"
    category: "Anthropic"
    type: feed
    url: "https://a.test/rss"
  - name: "Krebs"
    category: "Security"
    type: feed
    url: "https://k.test/rss"
  - name: "Events"
    category: "Anthropic"
    type: html
    url: "https://a.test/events"
    gate: new_only
"""


def test_load_config_reads_sources(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(CONFIG)
    cfg = load_config(p)
    assert len(cfg["sources"]) == 3
    assert cfg["telegram"]["include_blurb"] is False


def test_load_config_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_load_config_rejects_source_without_name(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("sources:\n  - category: X\n    type: feed\n    url: https://x.test\n")
    with pytest.raises(ConfigError, match="name"):
        load_config(p)


def test_load_config_rejects_unknown_type(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        'sources:\n  - name: X\n    category: X\n    type: carrier_pigeon\n    url: https://x.test\n'
    )
    with pytest.raises(ConfigError, match="carrier_pigeon"):
        load_config(p)


def test_category_order_follows_first_appearance(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(CONFIG)
    assert category_order(load_config(p)) == ["Anthropic", "Security"]


def test_gate_defaults_to_published(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(CONFIG)
    gates = gate_by_source(load_config(p))
    assert gates["Anthropic News"] == "published"
    assert gates["Events"] == "new_only"


def test_load_env_does_not_override_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("TECHNEWS_TELEGRAM_CHAT_ID", "from-shell")
    (tmp_path / ".env").write_text(
        "TECHNEWS_TELEGRAM_CHAT_ID=from-file\nTECHNEWS_TELEGRAM_BOT_TOKEN=tok\n"
    )
    load_env(tmp_path)
    assert os.environ["TECHNEWS_TELEGRAM_CHAT_ID"] == "from-shell"
    assert os.environ["TECHNEWS_TELEGRAM_BOT_TOKEN"] == "tok"


def test_load_env_ignores_comments_and_blanks(tmp_path, monkeypatch):
    monkeypatch.delenv("TECHNEWS_X", raising=False)
    (tmp_path / ".env").write_text("# comment\n\nTECHNEWS_X = 'quoted value' \n")
    load_env(tmp_path)
    assert os.environ["TECHNEWS_X"] == "quoted value"


def test_get_secret_missing_raises(monkeypatch):
    monkeypatch.delenv("TECHNEWS_TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="TECHNEWS_TELEGRAM_BOT_TOKEN"):
        get_secret("TECHNEWS_TELEGRAM_BOT_TOKEN")
