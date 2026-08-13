from datetime import date, datetime, timezone

from dispatchers.site import (
    prune_archive,
    render_archive_index,
    render_page,
    write_site,
)
from models import Article

UTC = timezone.utc
DAY = date(2026, 8, 13)


def make(headline, category="Security", source="Krebs", blurb="", link=None):
    return Article(
        category=category,
        source=source,
        headline=headline,
        link=link or f"https://x.test/{headline}",
        published=datetime(2026, 8, 13, 9, 30, tzinfo=UTC),
        blurb=blurb,
    )


def test_page_is_self_contained():
    # A weaker version of this test once only checked that "http://" (with
    # no "s") was absent from the page, aside from a whitelisted
    # "http://www.w3.org" substring that this renderer never even
    # produces. That proves nothing: every article link in this suite is
    # already an https:// URL, so the assertion passes trivially, and it
    # would still pass even if the page pulled in an external stylesheet,
    # font, or image over https. Self-containment means "no tag that
    # causes the browser to fetch something else automatically" -- so
    # check for the actual resource-loading constructs instead. A plain
    # <a href> to the article's own site is fine (a human clicks it); a
    # <link>, <img>, @import, or <script src> is not (the browser fetches
    # it unattended, which fails offline).
    html = render_page([make("one")], ["Security"], day=DAY)
    assert "<style>" in html
    assert "<link" not in html
    assert "<img" not in html
    assert "@import" not in html
    assert "<script src=" not in html
    assert "<script>" not in html


def test_page_escapes_markup_in_content():
    html = render_page([make("Bug in <script> & <iframe>")], ["Security"], day=DAY)
    assert "&lt;script&gt;" in html
    assert "<script>" not in html


def test_page_escapes_quotes_in_link_attribute():
    # Telegram's escape() deliberately skips quote escaping because
    # Telegram's HTML text nodes don't need it. Here the link goes into an
    # href="..." attribute, where a raw double quote lets a hostile source
    # break out of the attribute and inject markup. If quote escaping were
    # dropped, this attack would land and the assertion below would fail.
    malicious = 'https://x.test/"><script>alert(1)</script>'
    html = render_page([make("h", link=malicious)], ["Security"], day=DAY)
    assert "<script>alert(1)</script>" not in html


def test_page_groups_by_category_in_order():
    articles = [make("s", category="Security"), make("a", category="Anthropic")]
    html = render_page(articles, ["Anthropic", "Security"], day=DAY)
    assert html.index(">Anthropic<") < html.index(">Security<")


def test_page_shows_blurbs():
    html = render_page([make("h", blurb="Full detail here")], ["Security"], day=DAY)
    assert "Full detail here" in html


def test_page_links_to_the_original():
    html = render_page([make("h", link="https://k.test/p")], ["Security"], day=DAY)
    assert 'href="https://k.test/p"' in html


def test_page_supports_both_color_schemes():
    html = render_page([make("h")], ["Security"], day=DAY)
    assert "prefers-color-scheme: dark" in html


def test_page_header_shows_date_and_count():
    html = render_page([make("a"), make("b")], ["Security"], day=DAY)
    assert "13 Aug 2026" in html
    assert "2 stories" in html


def test_write_site_creates_index_and_archive(tmp_path):
    cfg = {"enabled": True, "output_dir": str(tmp_path), "keep_days": 30}
    index = write_site([make("a")], ["Security"], cfg, day=DAY)
    assert index == tmp_path / "index.html"
    assert index.exists()
    assert (tmp_path / "archive" / "2026-08-13.html").exists()
    assert (tmp_path / "archive" / "index.html").exists()


def test_write_site_expands_user_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = {"enabled": True, "output_dir": "~/site", "keep_days": 30}
    index = write_site([make("a")], ["Security"], cfg, day=DAY)
    assert index == tmp_path / "site" / "index.html"


def test_archive_index_lists_days_newest_first():
    html = render_archive_index([date(2026, 8, 11), date(2026, 8, 13)])
    assert html.index("2026-08-13") < html.index("2026-08-11")


def test_prune_removes_only_old_archive_files(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    for day in ("2026-08-13", "2026-07-01", "2026-08-01"):
        (archive / f"{day}.html").write_text("x")
    (archive / "index.html").write_text("keep me")

    removed = prune_archive(archive, keep_days=30, today=DAY)
    assert removed == 1
    assert not (archive / "2026-07-01.html").exists()
    assert (archive / "2026-08-01.html").exists()
    # The index matches the *.html glob just like every dated page does,
    # so this line only stays green if prune_archive actually skips
    # filenames it can't parse as a date rather than deleting everything
    # the glob matched.
    assert (archive / "index.html").exists()


def test_prune_ignores_unparseable_filenames(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "notes.html").write_text("x")
    assert prune_archive(archive, keep_days=1, today=DAY) == 0
    assert (archive / "notes.html").exists()
