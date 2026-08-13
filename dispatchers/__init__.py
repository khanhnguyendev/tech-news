"""Optional outputs that run only after the text digest is delivered."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from dispatchers import telegram, video
from models import Article, log


def make_extras(
    session,
    config: dict,
    *,
    day: date,
    token: str,
    chat_id: str,
    data_dir: Path,
):
    """Build the callable pipeline.run invokes after successful delivery.

    Every output is isolated: a failure is logged and the next one still
    runs. None of them can change the run's exit code -- the digest has
    already arrived by the time this is called.
    """
    video_cfg = config.get("video", {})

    def extras(articles: list[Article]) -> None:
        if video_cfg.get("enabled", False):
            try:
                path = video.generate(
                    articles, video_cfg, data_dir / "video", day=day
                )
                if path is not None and video_cfg.get("send_to_telegram", True):
                    caption = f"<b>TechNews — {day.strftime('%d %b %Y')}</b>"
                    telegram.send_video(session, token, chat_id, path, caption)
            except Exception as exc:  # noqa: BLE001 - never critical
                log.error("Video output failed: %s: %s", type(exc).__name__, exc)

    return extras
