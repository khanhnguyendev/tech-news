"""Silent slideshow recap with background music.

The timeline is the durable artifact: it records exactly what appeared on
screen and when, so subtitles or narration can be added later without
re-collecting anything.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from models import Article

COVER_SECONDS = 3.0


@dataclass(frozen=True)
class Segment:
    index: int
    start: float
    end: float
    category: str
    source: str
    headline: str
    link: str

    @property
    def duration(self) -> float:
        return self.end - self.start


def build_timeline(
    articles: list[Article],
    *,
    seconds_per_slide: float,
    cover_title: str,
    cover_seconds: float = COVER_SECONDS,
) -> list[Segment]:
    if not articles:
        return []

    segments = [
        Segment(
            index=0,
            start=0.0,
            end=cover_seconds,
            category="",
            source="",
            headline=cover_title,
            link="",
        )
    ]
    cursor = cover_seconds
    for position, article in enumerate(articles, start=1):
        segments.append(
            Segment(
                index=position,
                start=cursor,
                end=cursor + seconds_per_slide,
                category=article.category,
                source=article.source,
                headline=article.headline,
                link=article.link,
            )
        )
        cursor += seconds_per_slide
    return segments


def total_duration(segments: list[Segment]) -> float:
    return segments[-1].end if segments else 0.0


def _srt_timestamp(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def format_srt(segments: list[Segment]) -> str:
    blocks = []
    for position, segment in enumerate(segments, start=1):
        text = segment.headline
        if segment.source:
            text += f"\n({segment.source})"
        blocks.append(
            f"{position}\n"
            f"{_srt_timestamp(segment.start)} --> {_srt_timestamp(segment.end)}\n"
            f"{text}\n"
        )
    return "\n".join(blocks)


def timeline_json(segments: list[Segment]) -> str:
    return json.dumps([asdict(s) for s in segments], indent=2, ensure_ascii=False)
