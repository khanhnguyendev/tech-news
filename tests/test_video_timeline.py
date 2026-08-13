import json
from datetime import datetime, timezone

from dispatchers.video import (
    Segment,
    build_timeline,
    format_srt,
    timeline_json,
    total_duration,
)
from models import Article

UTC = timezone.utc


def make(headline, category="Security", source="Krebs"):
    return Article(
        category=category,
        source=source,
        headline=headline,
        link=f"https://x.test/{headline}",
        published=datetime(2026, 8, 13, tzinfo=UTC),
    )


def timeline(count=3, seconds_per_slide=4.0, cover_seconds=3.0):
    return build_timeline(
        [make(f"h{i}") for i in range(count)],
        seconds_per_slide=seconds_per_slide,
        cover_title="TechNews — 13 Aug 2026",
        cover_seconds=cover_seconds,
    )


def test_first_segment_is_the_cover():
    segments = timeline()
    assert segments[0].index == 0
    assert segments[0].headline == "TechNews — 13 Aug 2026"
    assert segments[0].start == 0.0
    assert segments[0].end == 3.0


def test_one_segment_per_article_after_the_cover():
    assert len(timeline(count=5)) == 6


def test_segments_are_contiguous_with_no_gaps():
    segments = timeline(count=4)
    for earlier, later in zip(segments, segments[1:]):
        assert earlier.end == later.start


def test_total_duration_is_cover_plus_slides():
    segments = timeline(count=3, seconds_per_slide=4.0, cover_seconds=3.0)
    assert total_duration(segments) == 3.0 + 3 * 4.0


def test_duration_property():
    assert timeline()[1].duration == 4.0


def test_empty_article_list_yields_no_timeline():
    assert build_timeline([], seconds_per_slide=4.0, cover_title="x") == []


def test_srt_is_correctly_numbered_and_formatted():
    srt = format_srt(timeline(count=1, seconds_per_slide=4.0, cover_seconds=3.0))
    lines = srt.strip().splitlines()
    assert lines[0] == "1"
    assert lines[1] == "00:00:00,000 --> 00:00:03,000"
    assert lines[2] == "TechNews — 13 Aug 2026"
    assert "00:00:03,000 --> 00:00:07,000" in srt


def test_srt_shows_source_under_the_headline():
    srt = format_srt(timeline(count=1))
    assert "Krebs" in srt


def test_srt_handles_hours():
    segments = [Segment(0, 3661.5, 3665.0, "C", "S", "Late story", "https://x.test/a")]
    assert "01:01:01,500 --> 01:01:05,000" in format_srt(segments)


def test_timeline_json_round_trips_every_field():
    segments = timeline(count=2)
    payload = json.loads(timeline_json(segments))
    assert len(payload) == 3
    assert payload[1]["link"] == "https://x.test/h0"
    assert payload[1]["source"] == "Krebs"
    assert payload[1]["category"] == "Security"
    assert payload[1]["start"] == 3.0
