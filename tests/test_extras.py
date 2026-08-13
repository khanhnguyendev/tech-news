from datetime import date, datetime, timezone
from pathlib import Path

import dispatchers
from dispatchers import make_extras
from models import Article

UTC = timezone.utc
DAY = date(2026, 8, 13)


def article(headline="h"):
    return Article("C", "S", headline, f"https://x.test/{headline}",
                   datetime(2026, 8, 13, tzinfo=UTC))


def config(**video_overrides):
    video = {"enabled": True, "seconds_per_slide": 4, "max_slides": 20,
             "resolution": [540, 960], "font": "", "music": "",
             "send_to_telegram": True}
    video.update(video_overrides)
    return {"video": video, "site": {"enabled": False}}


def test_video_is_generated_and_sent(tmp_path, monkeypatch):
    generated, sent = [], []
    monkeypatch.setattr(
        dispatchers.video, "generate",
        lambda articles, cfg, out_dir, **kw: (generated.append(len(articles))
                                              or Path(tmp_path / "recap.mp4")),
    )
    monkeypatch.setattr(
        dispatchers.telegram, "send_video",
        lambda session, token, chat_id, path, caption: sent.append(path),
    )
    extras = make_extras(None, config(), day=DAY, token="T", chat_id="C",
                         data_dir=tmp_path)
    extras([article("a"), article("b")])
    assert generated == [2]
    assert sent == [Path(tmp_path / "recap.mp4")]


def test_video_disabled_generates_nothing(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(
        dispatchers.video, "generate",
        lambda *a, **k: called.append(1),
    )
    extras = make_extras(None, config(enabled=False), day=DAY, token="T",
                         chat_id="C", data_dir=tmp_path)
    extras([article()])
    assert called == []


def test_video_not_sent_when_send_to_telegram_is_false(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(
        dispatchers.video, "generate", lambda *a, **k: Path(tmp_path / "recap.mp4")
    )
    monkeypatch.setattr(
        dispatchers.telegram, "send_video",
        lambda *a, **k: sent.append(1),
    )
    extras = make_extras(None, config(send_to_telegram=False), day=DAY,
                         token="T", chat_id="C", data_dir=tmp_path)
    extras([article()])
    assert sent == []


def test_skipped_video_is_not_sent(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(dispatchers.video, "generate", lambda *a, **k: None)
    monkeypatch.setattr(
        dispatchers.telegram, "send_video", lambda *a, **k: sent.append(1)
    )
    extras = make_extras(None, config(), day=DAY, token="T", chat_id="C",
                         data_dir=tmp_path)
    extras([article()])
    assert sent == []


def test_video_failure_is_logged_and_swallowed(tmp_path, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(dispatchers.video, "generate", boom)
    extras = make_extras(None, config(), day=DAY, token="T", chat_id="C",
                         data_dir=tmp_path)
    with caplog.at_level("ERROR", logger="technews"):
        extras([article()])
    assert "ffmpeg exploded" in caplog.text


def test_send_failure_is_logged_and_swallowed(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(
        dispatchers.video, "generate", lambda *a, **k: Path(tmp_path / "recap.mp4")
    )

    def boom(*a, **k):
        raise RuntimeError("upload rejected")

    monkeypatch.setattr(dispatchers.telegram, "send_video", boom)
    extras = make_extras(None, config(), day=DAY, token="T", chat_id="C",
                         data_dir=tmp_path)
    with caplog.at_level("ERROR", logger="technews"):
        extras([article()])
    assert "upload rejected" in caplog.text


def config_with_site(tmp_path, **site_overrides):
    cfg = config()
    cfg["video"]["enabled"] = False
    cfg["site"] = {"enabled": True, "output_dir": str(tmp_path / "site"),
                   "keep_days": 30}
    cfg["site"].update(site_overrides)
    cfg["sources"] = [{"name": "S", "category": "C", "type": "feed",
                       "url": "https://x.test"}]
    return cfg


def test_site_is_written(tmp_path):
    extras = make_extras(None, config_with_site(tmp_path), day=DAY, token="T",
                         chat_id="C", data_dir=tmp_path)
    extras([article("a")])
    assert (tmp_path / "site" / "index.html").exists()


def test_site_disabled_writes_nothing(tmp_path):
    cfg = config_with_site(tmp_path)
    cfg["site"]["enabled"] = False
    extras = make_extras(None, cfg, day=DAY, token="T", chat_id="C",
                         data_dir=tmp_path)
    extras([article("a")])
    assert not (tmp_path / "site").exists()


def test_video_failure_does_not_prevent_the_site(tmp_path, monkeypatch, caplog):
    cfg = config_with_site(tmp_path)
    cfg["video"]["enabled"] = True

    def boom(*a, **k):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(dispatchers.video, "generate", boom)
    extras = make_extras(None, cfg, day=DAY, token="T", chat_id="C",
                         data_dir=tmp_path)
    with caplog.at_level("ERROR", logger="technews"):
        extras([article("a")])
    assert (tmp_path / "site" / "index.html").exists()


def test_site_failure_is_logged_and_swallowed(tmp_path, monkeypatch, caplog):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(dispatchers.site, "write_site", boom)
    extras = make_extras(None, config_with_site(tmp_path), day=DAY, token="T",
                         chat_id="C", data_dir=tmp_path)
    with caplog.at_level("ERROR", logger="technews"):
        extras([article("a")])
    assert "disk full" in caplog.text
