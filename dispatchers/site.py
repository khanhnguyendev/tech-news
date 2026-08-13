"""A single self-contained HTML page plus a rolling archive.

No external assets, no JavaScript, no server required: the page opens
straight from the filesystem and renders identically offline.
"""

from __future__ import annotations

import html as html_module
from datetime import date, datetime, timedelta
from pathlib import Path
from string import Template

from models import Article, log

STYLE = """
:root { color-scheme: light dark;
        --bg:#ffffff; --fg:#1f2328; --muted:#656d76;
        --accent:#0969da; --card:#f6f8fa; --line:#d0d7de; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0d1117; --fg:#e6edf3; --muted:#8b949e;
          --accent:#58a6ff; --card:#161b22; --line:#30363d; }
}
* { box-sizing:border-box; }
body { margin:0; padding:2rem 1rem; background:var(--bg); color:var(--fg);
       font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
main { max-width:52rem; margin:0 auto; }
h1 { font-size:1.5rem; margin:0 0 .25rem; }
.count { color:var(--muted); margin:0 0 2rem; }
h2 { font-size:1rem; text-transform:uppercase; letter-spacing:.06em;
     color:var(--accent); margin:2rem 0 .75rem;
     padding-bottom:.35rem; border-bottom:1px solid var(--line); }
ul { list-style:none; padding:0; margin:0; }
li { background:var(--card); border:1px solid var(--line); border-radius:8px;
     padding:.75rem 1rem; margin-bottom:.6rem; }
a { color:var(--fg); text-decoration:none; font-weight:600; }
a:hover { color:var(--accent); text-decoration:underline; }
.meta { color:var(--muted); font-size:.85rem; margin-top:.25rem; }
.blurb { color:var(--muted); font-size:.9rem; margin-top:.4rem; }
footer { margin-top:3rem; color:var(--muted); font-size:.85rem; }
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


def render_page(articles: list[Article], category_order: list[str], *, day: date) -> str:
    pretty_day = day.strftime("%d %b %Y")
    noun = "story" if len(articles) == 1 else "stories"
    parts = [
        f"<h1>TechNews — {_esc(pretty_day)}</h1>",
        f'<p class="count">{len(articles)} {noun}</p>',
    ]
    for category in _ordered_categories(articles, category_order):
        parts.append(f"<h2>{_esc(category)}</h2>")
        parts.append("<ul>")
        for article in (a for a in articles if a.category == category):
            when = (
                article.published.strftime("%H:%M UTC") if article.published else "—"
            )
            item = [
                "<li>",
                f'<a href="{_esc(article.link)}">{_esc(article.headline)}</a>',
                f'<div class="meta">{_esc(article.source)} · {_esc(when)}</div>',
            ]
            if article.blurb:
                item.append(f'<div class="blurb">{_esc(article.blurb)}</div>')
            item.append("</li>")
            parts.append("".join(item))
        parts.append("</ul>")
    parts.append('<footer><a href="archive/index.html">Archive</a></footer>')
    return _page(f"TechNews — {pretty_day}", "\n".join(parts))


def render_archive_index(days: list[date]) -> str:
    items = "\n".join(
        f'<li><a href="{d.isoformat()}.html">{d.isoformat()}</a></li>'
        for d in sorted(days, reverse=True)
    )
    body = f"<h1>TechNews archive</h1>\n<ul>\n{items}\n</ul>"
    return _page("TechNews archive", body)


def prune_archive(archive_dir: Path, keep_days: int, today: date) -> int:
    """Delete archive pages older than keep_days. Returns how many went.

    Only files whose name is a bare YYYY-MM-DD.html date are candidates.
    The glob also matches archive/index.html and anything else dropped in
    the directory, but strptime rejects any stem that isn't a plain date,
    so those are skipped rather than deleted -- this function must never
    touch a file it did not create itself.
    """
    cutoff = today - timedelta(days=keep_days)
    removed = 0
    for path in archive_dir.glob("*.html"):
        try:
            day = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            path.unlink()
            removed += 1
    if removed:
        log.info("Site: pruned %d archived page(s)", removed)
    return removed


def write_site(
    articles: list[Article], category_order: list[str], cfg: dict, *, day: date
) -> Path:
    out_dir = Path(cfg.get("output_dir", "~/.technews/site")).expanduser()
    archive_dir = out_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    html = render_page(articles, category_order, day=day)
    index_path = out_dir / "index.html"
    index_path.write_text(html, encoding="utf-8")
    (archive_dir / f"{day.isoformat()}.html").write_text(html, encoding="utf-8")

    prune_archive(archive_dir, int(cfg.get("keep_days", 30)), day)

    days = []
    for path in archive_dir.glob("*.html"):
        try:
            days.append(datetime.strptime(path.stem, "%Y-%m-%d").date())
        except ValueError:
            continue
    (archive_dir / "index.html").write_text(
        render_archive_index(days), encoding="utf-8"
    )

    log.info("Site: wrote %s", index_path)
    return index_path
