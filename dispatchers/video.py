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
MUTED = (125, 133, 144)
GHOST = (28, 34, 44)
RULE = (40, 47, 58)
TRACK = (35, 41, 51)
MARGIN_RATIO = 0.056

# One hue per category. In a video the colour is a functional signal for
# "which group am I in", not decoration, so the single-accent rule that
# governs the page is deliberately relaxed here. Anything unlisted falls
# back to the neutral accent rather than inventing a colour.
CATEGORY_COLOURS = {
    "Anthropic": (199, 143, 255),
    "YouTube": (255, 86, 86),
    "Security": (255, 159, 64),
    "Trending": (94, 214, 143),
    "Releases": (120, 180, 255),
    "Apple": (200, 208, 218),
    "Electron": (128, 220, 255),
}


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
    crossfade_seconds: float = 0.0,
) -> list[Segment]:
    if not articles:
        return []

    # With a crossfade each slide begins while the previous one is still
    # on screen, so the finished video is (n-1)*fade shorter than the sum
    # of its slides. Subtracting that here keeps recap.srt aligned with
    # what is actually rendered; a timeline that ignored it would drift a
    # little further out with every slide.
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
    cursor = cover_seconds - crossfade_seconds
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
        cursor += seconds_per_slide - crossfade_seconds
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


def fit_title_font(headline, draw, font_path, *, usable, region, width):
    """Pick the largest title size whose wrapped lines still fit `region`.

    A fixed size leaves a two-line headline floating in an otherwise empty
    1920px frame, and forces a long one to overflow. Fitting to the space
    makes short headlines strike and long ones still land.
    """
    largest = int(width * 0.135)
    smallest = int(width * 0.052)
    chosen = load_font(font_path, smallest)
    lines = wrap_text(headline, chosen, usable, draw)
    for size in range(largest, smallest - 1, -4):
        font = load_font(font_path, size)
        candidate = wrap_text(headline, font, usable, draw)
        fits_height = len(candidate) * int(size * 1.19) <= region
        # Every line must also fit across, because wrap_text admits the
        # first word of a line whatever its width. Without this a long
        # word runs off the right edge at large sizes.
        fits_width = all(draw.textlength(l, font=font) <= usable for l in candidate)
        if fits_height and fits_width:
            return font, candidate
        chosen, lines = font, candidate
    return chosen, lines


def render_segment(
    segment: Segment, *, resolution, font_path, total: int | None = None
) -> Image.Image:
    """One slide.

    The previous layout floated three left-aligned blocks at three heights
    with roughly seventy percent of the frame empty, and nothing told the
    viewer how far through the recap they were. This one anchors the frame
    on a large ghosted index, separates the label from the headline with a
    hairline, and carries a progress bar along the bottom.
    """
    width, height = resolution
    margin = int(width * MARGIN_RATIO)
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    accent = CATEGORY_COLOURS.get(segment.category, ACCENT)
    is_cover = segment.index == 0
    usable = width - 2 * margin

    if is_cover:
        title_font = load_font(font_path, int(width * 0.115))
        lines = wrap_text(segment.headline, title_font, usable, draw)
        line_height = int(title_font.size * 1.16)
        y = (height - len(lines) * line_height) // 2
        for line in lines:
            draw.text((margin, y), line, font=title_font, fill=FOREGROUND)
            y += line_height
        if segment.source:
            small = load_font(font_path, int(width * 0.038))
            draw.text((margin, y + line_height // 2), segment.source,
                      font=small, fill=MUTED)
        return image

    # The index is the anchor: large enough to hold the eye, dark enough
    # to stay behind the headline rather than compete with it.
    ghost = load_font(font_path, int(width * 0.26))
    draw.text((margin, int(height * 0.055)), f"{segment.index:02d}",
              font=ghost, fill=GHOST)

    label_font = load_font(font_path, int(width * 0.036))
    label_y = int(height * 0.205)
    draw.text((margin, label_y), segment.category.upper(), font=label_font,
              fill=accent)
    rule_y = label_y + int(label_font.size * 1.7)
    draw.line([(margin, rule_y), (width - margin, rule_y)], fill=RULE, width=2)

    # The headline block is centred in the space between the rule and the
    # source line rather than hung from the rule. A two-line headline on a
    # top-anchored layout leaves the lower half of a 1920px frame empty,
    # which is the flaw this redesign exists to fix.
    source_y = height - int(height * 0.083)
    region_top = rule_y + int(height * 0.03)
    region_bottom = source_y - int(height * 0.03)
    title_font, lines = fit_title_font(
        segment.headline, draw, font_path,
        usable=usable, region=region_bottom - region_top, width=width,
    )
    line_height = int(title_font.size * 1.19)
    block = len(lines) * line_height
    y = max(region_top, region_top + (region_bottom - region_top - block) // 2)
    for line in lines:
        draw.text((margin, y), line, font=title_font, fill=FOREGROUND)
        y += line_height

    if segment.source:
        small = load_font(font_path, int(width * 0.037))
        draw.text((margin, source_y), segment.source, font=small, fill=MUTED)

    if total and total > 1:
        bar_y = height - int(height * 0.036)
        filled = margin + (width - 2 * margin) * segment.index / (total - 1)
        draw.line([(margin, bar_y), (width - margin, bar_y)], fill=TRACK, width=6)
        draw.line([(margin, bar_y), (filled, bar_y)], fill=accent, width=6)
    return image


def write_slides(segments, out_dir: Path, *, resolution, font_path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for segment in segments:
        path = out_dir / f"slide_{segment.index:03d}.png"
        render_segment(
            segment, resolution=resolution, font_path=font_path,
            total=len(segments),
        ).save(path)
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


def ffmpeg_args(
    *,
    concat_path,
    music_path,
    out_path,
    duration,
    resolution,
    slides=None,
    durations=None,
    crossfade: float = 0.0,
) -> list[str]:
    """Build the render command.

    Two shapes, because the concat demuxer cannot cross-fade. Without a
    crossfade it stays the cheaper path: one input, per-image durations.
    With one, each slide becomes its own looped input and the boundaries
    are chained through xfade filters, whose offsets have to account for
    every earlier fade or the transitions drift out of place.
    """
    width, height = resolution
    scale = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x0d1117,fps=25"
    )
    args = ["ffmpeg", "-y", "-loglevel", "error"]

    if crossfade > 0 and slides and durations:
        for path, seconds in zip(slides, durations):
            args += ["-loop", "1", "-t", f"{seconds:.3f}", "-i", str(path)]
        steps = []
        label = "0:v"
        elapsed = 0.0
        for index in range(1, len(slides)):
            elapsed += durations[index - 1]
            offset = elapsed - index * crossfade
            output = f"x{index}"
            steps.append(
                f"[{label}][{index}:v]xfade=transition=fade:"
                f"duration={crossfade:.3f}:offset={offset:.3f}[{output}]"
            )
            label = output
        steps.append(f"[{label}]{scale}[v]")
        args += ["-filter_complex", ";".join(steps), "-map", "[v]"]
        if music_path:
            args += ["-i", str(music_path), "-map", f"{len(slides)}:a",
                     "-c:a", "aac", "-b:a", "128k"]
    else:
        args += ["-f", "concat", "-safe", "0", "-i", str(concat_path)]
        if music_path:
            fade_start = max(0.0, duration - 3.0)
            args += [
                "-i", str(music_path),
                "-filter_complex", f"[1:a]afade=t=out:st={fade_start:.3f}:d=3[a]",
                "-map", "0:v", "-map", "[a]",
                "-c:a", "aac", "-b:a", "128k",
            ]
        args += ["-vf", scale]

    args += [
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

    crossfade = float(cfg.get("crossfade_seconds", 0))
    segments = build_timeline(
        selected,
        seconds_per_slide=float(cfg.get("seconds_per_slide", 4)),
        cover_title=f"TechNews {day.strftime('%d %b %Y')}",
        crossfade_seconds=crossfade,
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
        slides=slide_paths,
        durations=[s.duration for s in segments],
        crossfade=crossfade,
    )
    completed = runner(args, capture_output=True)
    if completed.returncode != 0:
        stderr = (completed.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(f"ffmpeg failed: {stderr}")

    log.info("Video: wrote %s (%d slides)", out_path, len(segments))
    return out_path
