"""A single self-contained HTML page plus a rolling archive.

No external assets, no JavaScript, no server required: the page opens
straight from the filesystem and renders identically offline.
"""

from __future__ import annotations

import html as html_module
from datetime import date, datetime, timedelta
from pathlib import Path
from string import Template

from models import Article, group_by_source, log

STYLE = """
:root { color-scheme: light dark;
        --bg:#ffffff; --fg:#1f2328; --muted:#656d76;
        --accent:#0969da; --line:#d0d7de; --rule:#e6e8eb; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0d1117; --fg:#e6edf3; --muted:#8b949e;
          --accent:#58a6ff; --line:#30363d; --rule:#21262d; }
}
* { box-sizing:border-box; }
body { margin:0; padding:3rem 1.25rem 4rem; background:var(--bg); color:var(--fg);
       font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,
            "Helvetica Neue",Arial,sans-serif;
       -webkit-text-size-adjust:100%; }
main { max-width:44rem; margin:0 auto; }
header.masthead { margin:0 0 3rem; }
h1 { font-size:1.35rem; font-weight:650; letter-spacing:-.015em; margin:0; }
.count { color:var(--muted); font-size:.9rem; margin:.3rem 0 0; }
section { margin:0 0 3rem; }
h2 { display:flex; align-items:baseline; gap:.5rem;
     font-size:.8rem; font-weight:600; text-transform:uppercase;
     letter-spacing:.09em; color:var(--muted); margin:0 0 1.25rem; }
h2 .tally { margin-left:auto; font-variant-numeric:tabular-nums;
            font-weight:400; letter-spacing:0; }
.group { margin:0 0 1.75rem; }
.group:last-child { margin-bottom:0; }
.source { font-size:.78rem; font-weight:600; color:var(--accent);
          letter-spacing:.01em; margin:0 0 .5rem; }
ul { list-style:none; padding:0; margin:0; }
li { padding:.7rem 0; border-top:1px solid var(--rule); }
li:first-child { border-top:0; padding-top:0; }
a.title { color:var(--fg); text-decoration:none; font-weight:500;
          display:inline-block; }
a.title:hover { color:var(--accent); text-decoration:underline;
                text-underline-offset:3px; }
a.title:focus-visible { outline:2px solid var(--accent); outline-offset:3px;
                        border-radius:2px; }
.time { display:block; color:var(--muted); font-size:.76rem;
        font-variant-numeric:tabular-nums; margin-top:.2rem; }
.blurb { color:var(--muted); font-size:.86rem; margin:.35rem 0 0;
         max-width:62ch; }
.blurb .metrics { font-variant-numeric:tabular-nums; }
footer { margin-top:4rem; padding-top:1.25rem; border-top:1px solid var(--line);
         color:var(--muted); font-size:.86rem; }
footer a { color:var(--accent); text-decoration:none; }
footer a:hover { text-decoration:underline; text-underline-offset:3px; }
@media (prefers-reduced-motion:reduce) {
  * { transition:none !important; animation:none !important; }
}
"""

# One template for the whole module: string.Template from the standard
# library is all this needs, so it doesn't justify a Jinja dependency.
PAGE_TEMPLATE = Template(
    "<!doctype html>\n"
    '<html lang="en">\n<head>\n'
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    "<title>$title</title>\n"
    "<style>$style</style>\n"
    "</head>\n<body>\n<main>\n"
    "$body\n"
    "</main>\n</body>\n</html>\n"
)


def _esc(text: str) -> str:
    # quote=True is deliberate: unlike telegram.escape() (whose output is a
    # Telegram HTML text node, where quotes are inert), everything rendered
    # here can land inside an attribute value such as href="...". A raw "
    # there would let a hostile source break out of the attribute.
    return html_module.escape(text or "", quote=True)


def _page(title: str, body: str) -> str:
    return PAGE_TEMPLATE.substitute(title=_esc(title), style=STYLE, body=body)


def _ordered_categories(articles, category_order):
    present = {a.category for a in articles}
    ordered = [c for c in category_order if c in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def render_page(
    articles: list[Article],
    category_order: list[str],
    *,
    day: date,
    icons: dict[str, str] | None = None,
    archive_href: str = "archive/index.html",
    today_href: str | None = None,
) -> str:
    """Render one page. `archive_href` and `today_href` are relative links,
    resolved from wherever this particular rendering is written to disk --
    the same HTML is never reused verbatim at two different locations
    (out_dir/index.html vs. out_dir/archive/{day}.html have different
    relative paths back to the same targets), so write_site() renders
    twice with different link targets rather than writing one string to
    both places.
    """
    icons = icons or {}
    pretty_day = day.strftime("%d %b %Y")
    noun = "story" if len(articles) == 1 else "stories"
    parts = [
        '<header class="masthead">',
        f"<h1>TechNews</h1>",
        f'<p class="count">{_esc(pretty_day)} · {len(articles)} {noun}</p>',
        "</header>",
    ]
    for category in _ordered_categories(articles, category_order):
        in_category = group_by_source([a for a in articles if a.category == category])
        icon = icons.get(category, "")
        label = f"{_esc(icon)} {_esc(category)}" if icon else _esc(category)
        parts.append("<section>")
        parts.append(
            f'<h2>{label}<span class="tally">{len(in_category)}</span></h2>'
        )
        open_source = None
        for article in in_category:
            if article.source != open_source:
                if open_source is not None:
                    parts.append("</ul></div>")
                parts.append(
                    f'<div class="group"><p class="source">{_esc(article.source)}</p><ul>'
                )
                open_source = article.source
            item = [
                "<li>",
                f'<a class="title" href="{_esc(article.link)}">'
                f"{_esc(article.headline)}</a>",
            ]
            if article.published is not None:
                item.append(
                    f'<span class="time">{_esc(article.published.strftime("%H:%M UTC"))}'
                    "</span>"
                )
            # A blurb may carry metrics on the first line and prose on the
            # second (trending repos do). Tabular figures are applied only
            # to the metrics line, so star counts line up column-wise.
            blurb_lines = article.blurb.split("\n") if article.blurb else []
            for index, line in enumerate(blurb_lines):
                css = "blurb metrics" if index == 0 and len(blurb_lines) > 1 else "blurb"
                item.append(f'<p class="{css}">{_esc(line)}</p>')
            item.append("</li>")
            parts.append("".join(item))
        if open_source is not None:
            parts.append("</ul></div>")
        parts.append("</section>")
    footer_links = [f'<a href="{_esc(archive_href)}">Archive</a>']
    if today_href is not None:
        footer_links.append(f'<a href="{_esc(today_href)}">Today</a>')
    parts.append(f"<footer>{' · '.join(footer_links)}</footer>")
    return _page(f"TechNews {pretty_day}", "\n".join(parts))


def render_archive_index(days: list[date]) -> str:
    items = "\n".join(
        f'<li><a href="{d.isoformat()}.html">{d.isoformat()}</a></li>'
        for d in sorted(days, reverse=True)
    )
    body = f"<h1>TechNews archive</h1>\n<ul>\n{items}\n</ul>"
    return _page("TechNews archive", body)


def _dated_archive_pages(archive_dir: Path) -> list[date]:
    """Every archive/*.html file whose stem is a plain YYYY-MM-DD date.

    Shared by prune_archive() and write_site() so the "which files are
    ours" rule -- a parseable date stem, nothing else -- lives in exactly
    one place. archive/index.html and any stray non-date file are
    silently excluded, the same protection prune_archive() relies on to
    never delete a file it didn't create.
    """
    days = []
    for path in archive_dir.glob("*.html"):
        try:
            days.append(datetime.strptime(path.stem, "%Y-%m-%d").date())
        except ValueError:
            continue
    return days


def prune_archive(archive_dir: Path, keep_days: int, today: date) -> int:
    """Delete archive pages older than keep_days. Returns how many went."""
    cutoff = today - timedelta(days=keep_days)
    removed = 0
    for day in _dated_archive_pages(archive_dir):
        if day < cutoff:
            (archive_dir / f"{day.isoformat()}.html").unlink()
            removed += 1
    if removed:
        log.info("Site: pruned %d archived page(s)", removed)
    return removed


def write_site(
    articles: list[Article],
    category_order: list[str],
    cfg: dict,
    *,
    day: date,
    icons: dict[str, str] | None = None,
) -> Path:
    out_dir = Path(cfg.get("output_dir", "~/.technews/site")).expanduser()
    archive_dir = out_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    # Rendered twice, not written-once-and-reused: out_dir/index.html and
    # archive_dir/{day}.html sit in different directories, so the relative
    # link back to the archive index (and, from the archive, back to
    # today) is a different string at each location. Re-rendering keeps
    # every link honest without ever touching already-escaped HTML.
    today_html = render_page(articles, category_order, day=day, icons=icons)
    index_path = out_dir / "index.html"
    index_path.write_text(today_html, encoding="utf-8")

    archived_html = render_page(
        articles,
        category_order,
        day=day,
        icons=icons,
        archive_href="index.html",
        today_href="../index.html",
    )
    (archive_dir / f"{day.isoformat()}.html").write_text(
        archived_html, encoding="utf-8"
    )

    prune_archive(archive_dir, int(cfg.get("keep_days", 30)), day)

    (archive_dir / "index.html").write_text(
        render_archive_index(_dated_archive_pages(archive_dir)), encoding="utf-8"
    )

    log.info("Site: wrote %s", index_path)
    return index_path
