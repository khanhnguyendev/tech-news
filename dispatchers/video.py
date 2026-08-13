"""Silent slideshow recap with background music.

The timeline is the durable artifact: it records exactly what appeared on
screen and when, so subtitles or narration can be added later without
re-collecting anything.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from models import Article, log

COVER_SECONDS = 3.0

BACKGROUND = (13, 17, 23)
FOREGROUND = (230, 237, 243)
ACCENT = (88, 166, 255)
MUTED = (139, 148, 158)
MARGIN = 40


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


def load_font(path: str | Path, size: int):
    """Load a TrueType font, falling back to Pillow's built-in font.

    The fallback keeps the module usable (and testable) on machines without
    the configured font rather than failing the whole video step. Pillow's
    built-in fallback honors the requested size (it loads a bundled TTF when
    a size is given), so wrapped text measured against it still matches what
    gets drawn.
    """
    try:
        return ImageFont.truetype(str(path), size)
    except (OSError, ValueError):
        return ImageFont.load_default(size=size)


def wrap_text(text: str, font, max_width: int, draw) -> list[str]:
    # A single word wider than max_width is placed on its own line
    # unconditionally rather than being hyphen-broken; real headlines are
    # made of ordinary space-separated words, so this is not expected in
    # practice, but it means a pathological single token can render past
    # the slide's margin.
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def render_segment(segment: Segment, *, resolution, font_path) -> Image.Image:
    width, height = resolution
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)

    is_cover = segment.index == 0
    title_font = load_font(font_path, max(28, width // (12 if is_cover else 18)))
    small_font = load_font(font_path, max(16, width // 34))
    usable = width - 2 * MARGIN

    if segment.category:
        draw.text((MARGIN, MARGIN), segment.category.upper(), font=small_font, fill=ACCENT)

    lines = wrap_text(segment.headline, title_font, usable, draw)
    line_height = title_font.size + 12
    block_height = len(lines) * line_height
    y = max(MARGIN * 3, (height - block_height) // 2)
    for line in lines:
        draw.text((MARGIN, y), line, font=title_font, fill=FOREGROUND)
        y += line_height

    if segment.source:
        draw.text(
            (MARGIN, height - MARGIN - small_font.size),
            segment.source,
            font=small_font,
            fill=MUTED,
        )
    return image


def write_slides(segments, out_dir: Path, *, resolution, font_path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for segment in segments:
        path = out_dir / f"slide_{segment.index:03d}.png"
        render_segment(segment, resolution=resolution, font_path=font_path).save(path)
        paths.append(path)
    return paths


def concat_manifest(slide_paths: list[Path], segments: list[Segment]) -> str:
    """Build an ffmpeg concat demuxer manifest.

    The concat demuxer applies a file's "duration" line only when it is
    followed by another "file" entry -- a duration on the very last entry is
    otherwise silently dropped, which would make the last slide flash by and
    leave the rendered video shorter than the timeline says it should be.
    The standard workaround is to repeat the final image once more, without
    a duration, purely so the preceding duration line takes effect.
    """
    lines = []
    for path, segment in zip(slide_paths, segments):
        lines.append(f"file '{path}'")
        lines.append(f"duration {segment.duration:.3f}")
    lines.append(f"file '{slide_paths[-1]}'")
    return "\n".join(lines) + "\n"


def ffmpeg_args(*, concat_path, music_path, out_path, duration, resolution) -> list[str]:
    """Build the ffmpeg argument list. Pure -- does not execute anything.

    The output duration is always pinned with an explicit ``-t`` equal to
    the timeline's total duration, which is the authoritative length of the
    slideshow. ``-shortest`` is deliberately not used: combined with ``-t``
    it would let a music track shorter than the slideshow truncate the
    video the moment the audio ran out, silently dropping trailing slides.
    With ``-t`` alone, a short track just leaves the tail of the video
    silent, and a long track gets trimmed to the video's length -- the
    slideshow's own timing always wins.
    """
    width, height = resolution
    args = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_path),
    ]
    if music_path:
        fade_start = max(0.0, duration - 3.0)
        args += [
            "-i", str(music_path),
            "-filter_complex", f"[1:a]afade=t=out:st={fade_start:.3f}:d=3[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:a", "aac", "-b:a", "128k",
        ]
    args += [
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x0d1117,fps=25",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-t", f"{duration:.3f}",
        str(out_path),
    ]
    return args


def generate(
    articles: list[Article],
    cfg: dict,
    out_dir: Path,
    *,
    day: date,
    runner=subprocess.run,
) -> Path | None:
    """Render the recap. Returns the mp4 path, or None when skipped."""
    if not articles:
        log.info("Video: nothing to render")
        return None
    if shutil.which("ffmpeg") is None:
        log.warning("Video: ffmpeg not found on PATH; skipping video generation")
        return None

    max_slides = int(cfg.get("max_slides", 20))
    selected = articles[:max_slides]
    if len(articles) > max_slides:
        log.info("Video: dropped %d item(s) beyond max_slides", len(articles) - max_slides)

    segments = build_timeline(
        selected,
        seconds_per_slide=float(cfg.get("seconds_per_slide", 4)),
        cover_title=f"TechNews — {day.strftime('%d %b %Y')}",
    )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "recap.json").write_text(timeline_json(segments), encoding="utf-8")
    (out_dir / "recap.srt").write_text(format_srt(segments), encoding="utf-8")

    resolution = tuple(cfg.get("resolution", [1080, 1920]))
    slides_dir = out_dir / "slides"
    slide_paths = write_slides(
        segments, slides_dir, resolution=resolution, font_path=cfg.get("font", "")
    )

    concat_path = out_dir / "concat.txt"
    concat_path.write_text(concat_manifest(slide_paths, segments), encoding="utf-8")

    music = cfg.get("music") or ""
    music_path = Path(music).expanduser() if music else None
    if music_path and not music_path.is_file():
        log.info("Video: music file %s not found; rendering silent video", music_path)
        music_path = None

    out_path = out_dir / "recap.mp4"
    args = ffmpeg_args(
        concat_path=concat_path,
        music_path=music_path,
        out_path=out_path,
        duration=total_duration(segments),
        resolution=resolution,
    )
    completed = runner(args, capture_output=True)
    if completed.returncode != 0:
        stderr = (completed.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(f"ffmpeg failed: {stderr}")

    log.info("Video: wrote %s (%d slides)", out_path, len(segments))
    return out_path
