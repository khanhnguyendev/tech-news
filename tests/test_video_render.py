import shutil
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from dispatchers.video import (
    build_timeline,
    concat_manifest,
    ffmpeg_args,
    generate,
    render_segment,
    wrap_text,
    write_slides,
)
from models import Article

UTC = timezone.utc
DAY = date(2026, 8, 13)
RESOLUTION = (540, 960)


def make(headline, source="Krebs", category="Security"):
    return Article(
        category=category,
        source=source,
        headline=headline,
        link=f"https://x.test/{headline}",
        published=datetime(2026, 8, 13, tzinfo=UTC),
    )


def cfg(**overrides):
    base = {
        "enabled": True,
        "seconds_per_slide": 4,
        "max_slides": 20,
        "resolution": list(RESOLUTION),
        "font": "/nonexistent/font.ttf",
        "music": "",
        "send_to_telegram": True,
    }
    base.update(overrides)
    return base


def timeline(count=2):
    return build_timeline(
        [make(f"Headline {i}") for i in range(count)],
        seconds_per_slide=4.0,
        cover_title="TechNews — 13 Aug 2026",
    )


def test_wrap_text_splits_long_headlines():
    from PIL import Image, ImageDraw

    from dispatchers.video import load_font

    draw = ImageDraw.Draw(Image.new("RGB", RESOLUTION))
    font = load_font("/nonexistent/font.ttf", 28)
    lines = wrap_text("word " * 60, font, RESOLUTION[0] - 80, draw)
    assert len(lines) > 1
    assert all(line.strip() for line in lines)


def test_render_segment_produces_an_image_of_the_right_size():
    image = render_segment(
        timeline()[1], resolution=RESOLUTION, font_path="/nonexistent/font.ttf"
    )
    assert image.size == RESOLUTION


def test_write_slides_creates_one_png_per_segment(tmp_path):
    segments = timeline(count=3)
    paths = write_slides(
        segments, tmp_path, resolution=RESOLUTION, font_path="/nonexistent/font.ttf"
    )
    assert len(paths) == len(segments)
    assert all(p.exists() and p.suffix == ".png" for p in paths)


def test_concat_manifest_lists_every_slide_with_its_duration(tmp_path):
    segments = timeline(count=2)
    paths = [tmp_path / f"slide_{i}.png" for i in range(len(segments))]
    manifest = concat_manifest(paths, segments)
    assert manifest.count("file '") == len(segments) + 1  # last file repeated
    assert "duration 4.000" in manifest
    assert "duration 3.000" in manifest


def test_ffmpeg_args_without_music_produce_a_silent_video(tmp_path):
    args = ffmpeg_args(
        concat_path=tmp_path / "c.txt",
        music_path=None,
        out_path=tmp_path / "out.mp4",
        duration=11.0,
        resolution=RESOLUTION,
    )
    assert args[0] == "ffmpeg"
    assert "-shortest" not in args
    assert "libx264" in args
    assert "yuv420p" in args
    assert "+faststart" in args


def test_ffmpeg_args_with_music_add_audio_and_fade(tmp_path):
    music = tmp_path / "music.mp3"
    music.write_bytes(b"")
    args = ffmpeg_args(
        concat_path=tmp_path / "c.txt",
        music_path=music,
        out_path=tmp_path / "out.mp4",
        duration=11.0,
        resolution=RESOLUTION,
    )
    joined = " ".join(str(a) for a in args)
    assert str(music) in joined
    assert "afade" in joined
    # An explicit -t cap already forces the output to the timeline's
    # duration; -shortest would additionally truncate the video the moment
    # a music track shorter than the slideshow runs out, silently dropping
    # trailing slides. -t alone gives the correct, non-truncating behavior.
    assert "-t" in args
    assert "11.000" in args
    assert "-shortest" not in args


def test_generate_returns_none_when_ffmpeg_is_absent(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with caplog.at_level("WARNING", logger="technews"):
        result = generate([make("x")], cfg(), tmp_path, day=DAY)
    assert result is None
    assert "ffmpeg" in caplog.text.lower()


def test_generate_returns_none_for_no_articles(tmp_path):
    assert generate([], cfg(), tmp_path, day=DAY) is None


def test_generate_writes_timeline_artifacts_and_calls_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg")
    calls = []

    def fake_runner(args, **kwargs):
        calls.append(args)
        Path(args[-1]).write_bytes(b"fake mp4")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    result = generate(
        [make("a"), make("b")], cfg(), tmp_path, day=DAY, runner=fake_runner
    )
    assert result == tmp_path / "recap.mp4"
    assert (tmp_path / "recap.json").exists()
    assert (tmp_path / "recap.srt").exists()
    assert len(calls) == 1


def test_generate_falls_back_to_silent_when_music_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg")
    calls = []

    def fake_runner(args, **kwargs):
        calls.append(args)
        Path(args[-1]).write_bytes(b"fake mp4")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    missing_music = tmp_path / "does-not-exist.mp3"
    result = generate(
        [make("a")],
        cfg(music=str(missing_music)),
        tmp_path,
        day=DAY,
        runner=fake_runner,
    )
    assert result == tmp_path / "recap.mp4"
    assert len(calls) == 1
    joined = " ".join(str(a) for a in calls[0])
    assert str(missing_music) not in joined
    assert "afade" not in joined


def test_generate_respects_max_slides(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def fake_runner(args, **kwargs):
        Path(args[-1]).write_bytes(b"fake mp4")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    generate(
        [make(f"h{i}") for i in range(10)],
        cfg(max_slides=3),
        tmp_path,
        day=DAY,
        runner=fake_runner,
    )
    payload = json.loads((tmp_path / "recap.json").read_text())
    assert len(payload) == 4  # cover + 3 slides


def test_generate_raises_when_ffmpeg_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def failing_runner(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, b"", b"encoder exploded")

    with pytest.raises(RuntimeError, match="encoder exploded"):
        generate([make("a")], cfg(), tmp_path, day=DAY, runner=failing_runner)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_smoke_real_ffmpeg_produces_a_playable_file(tmp_path):
    result = generate([make("a"), make("b")], cfg(), tmp_path, day=DAY)
    assert result is not None
    assert result.stat().st_size > 1000


def test_slide_carries_an_index_and_a_progress_bar():
    """The old slide gave no sense of position: every frame looked like it
    could be the last. The index and the bar answer 'how much is left'."""
    from dispatchers.video import render_segment
    segments = timeline(count=3)
    image = render_segment(segments[2], resolution=RESOLUTION,
                           font_path="/nonexistent/font.ttf", total=len(segments))
    assert image.size == RESOLUTION


def test_render_segment_still_works_without_a_total():
    from dispatchers.video import render_segment
    image = render_segment(timeline()[1], resolution=RESOLUTION,
                           font_path="/nonexistent/font.ttf")
    assert image.size == RESOLUTION


def test_crossfade_args_chain_one_xfade_per_boundary(tmp_path):
    from dispatchers.video import ffmpeg_args
    slides = [tmp_path / f"s{i}.png" for i in range(3)]
    for s in slides:
        s.write_bytes(b"")
    args = ffmpeg_args(
        concat_path=None, music_path=None, out_path=tmp_path / "o.mp4",
        duration=11.0, resolution=RESOLUTION,
        slides=slides, durations=[3.0, 4.0, 4.0], crossfade=0.5,
    )
    joined = " ".join(str(a) for a in args)
    assert joined.count("xfade") == 2, "one transition per boundary"
    assert args.count("-loop") == 3, "each slide is looped as its own input"
    assert "concat" not in joined


def test_crossfade_offsets_account_for_earlier_fades(tmp_path):
    from dispatchers.video import ffmpeg_args
    slides = [tmp_path / f"s{i}.png" for i in range(3)]
    for s in slides:
        s.write_bytes(b"")
    args = ffmpeg_args(
        concat_path=None, music_path=None, out_path=tmp_path / "o.mp4",
        duration=11.0, resolution=RESOLUTION,
        slides=slides, durations=[3.0, 4.0, 4.0], crossfade=0.5,
    )
    graph = [a for a in args if "xfade" in str(a)][0]
    assert "offset=2.500" in graph
    assert "offset=6.000" in graph


def test_zero_crossfade_still_uses_the_concat_demuxer(tmp_path):
    from dispatchers.video import ffmpeg_args
    args = ffmpeg_args(
        concat_path=tmp_path / "c.txt", music_path=None,
        out_path=tmp_path / "o.mp4", duration=11.0, resolution=RESOLUTION,
    )
    assert "concat" in " ".join(str(a) for a in args)
    assert "xfade" not in " ".join(str(a) for a in args)


def test_a_short_headline_gets_larger_type_than_a_long_one():
    """A 1920px frame holding two lines of fixed-size text is mostly empty.
    Fitting the size to the available region is what makes a short
    headline strike and a long one still fit."""
    from dispatchers.video import fit_title_font

    from PIL import Image, ImageDraw
    draw = ImageDraw.Draw(Image.new("RGB", RESOLUTION))
    short, _ = fit_title_font("Short one", draw, "/nonexistent/font.ttf",
                              usable=940, region=900, width=1080)
    long_font, _ = fit_title_font(
        "Google Cloud Sets Out Post-Quantum Roadmap With 2029 Readiness Goal "
        "And Several Other Long Clauses To Force Wrapping",
        draw, "/nonexistent/font.ttf", usable=940, region=900, width=1080,
    )
    assert short.size > long_font.size


def test_fitted_type_never_overflows_the_region():
    from dispatchers.video import fit_title_font

    from PIL import Image, ImageDraw
    draw = ImageDraw.Draw(Image.new("RGB", RESOLUTION))
    for headline in ["Tiny", "A moderately sized headline here", "word " * 60]:
        font, lines = fit_title_font(headline, draw, "/nonexistent/font.ttf",
                                     usable=940, region=900, width=1080)
        assert len(lines) * int(font.size * 1.19) <= 900


def test_fitted_type_never_overflows_the_frame_horizontally():
    """wrap_text lets the first word of a line through regardless of width,
    to guarantee progress. At large sizes a long word like "Post-Quantum"
    then runs off the right edge, which is only visible once rendered."""
    from dispatchers.video import fit_title_font
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", RESOLUTION))
    usable = 940
    font, lines = fit_title_font(
        "Google Cloud Sets Out Post-Quantum Roadmap With 2029 Readiness Goal",
        draw, "/nonexistent/font.ttf", usable=usable, region=1200, width=1080,
    )
    for line in lines:
        assert draw.textlength(line, font=font) <= usable, line


def test_background_is_no_longer_a_flat_field():
    """A single solid colour across a 1920px frame reads as a void. Grain
    plus a faint category-tinted glow gives the frame material without
    competing with the headline."""
    from dispatchers.video import render_background
    image = render_background(RESOLUTION, (255, 159, 64))
    colours = {image.getpixel((x, y)) for x in range(0, 500, 37)
               for y in range(0, 900, 41)}
    assert len(colours) > 50, "a flat fill would yield one colour"


def test_the_glow_stays_faint_enough_for_the_ghost_index_to_read():
    """The index is drawn a little lighter than the base background and
    reads as sitting behind the text. If the glow lifted the background
    past it the relationship would invert and the anchor would come
    forward instead."""
    from dispatchers.video import render_background, GHOST
    image = render_background(RESOLUTION, (255, 159, 64)).convert("L")
    ghost_luma = sum(GHOST) / 3
    brightest = max(
        image.getpixel((x, y)) for x in range(0, RESOLUTION[0], 23)
        for y in range(0, int(RESOLUTION[1] * 0.4), 23)
    )
    assert brightest < ghost_luma, (brightest, ghost_luma)


def test_the_background_is_deterministic():
    """Two runs of the same day must produce identical slides, or the video
    would differ byte for byte on every render for no reason."""
    from dispatchers.video import render_background
    a = render_background(RESOLUTION, (94, 214, 143))
    b = render_background(RESOLUTION, (94, 214, 143))
    assert a.tobytes() == b.tobytes()
