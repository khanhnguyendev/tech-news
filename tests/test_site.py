import re
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


def test_page_unknown_category_appears_after_known_ones():
    # config.yaml's category order equals first-appearance order; a
    # category that isn't in that list (e.g. a newly added source whose
    # category the config hasn't caught up to yet) must still render --
    # just after all the categories the config does know about.
    articles = [
        make("s", category="Security"),
        make("z", category="Zzz-Unlisted"),
        make("a", category="Anthropic"),
    ]
    html = render_page(articles, ["Anthropic", "Security"], day=DAY)
    assert ">Anthropic<" in html and ">Security<" in html and ">Zzz-Unlisted<" in html
    last_known = max(html.index(">Anthropic<"), html.index(">Security<"))
    assert html.index(">Zzz-Unlisted<") > last_known


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


def _footer_hrefs(html):
    footer = re.search(r"<footer>(.*?)</footer>", html, re.S).group(1)
    return re.findall(r'href="([^"]+)"', footer)


def test_write_site_links_resolve_from_their_own_directory(tmp_path):
    # write_site() writes the *same* article set to two different
    # directories: out_dir/index.html and out_dir/archive/{day}.html. A
    # relative link that is correct from one location is generally wrong
    # from the other -- out_dir/index.html's "archive/index.html" would
    # resolve, from inside archive/, to the nonexistent
    # archive/archive/index.html. This walks the *actual* hrefs the
    # renderer produced (not a hardcoded guess at what they should be)
    # and resolves each one against the file that actually contains it,
    # so it fails if the two pages ever go back to sharing one rendered
    # string.
    cfg = {"enabled": True, "output_dir": str(tmp_path), "keep_days": 30}
    write_site([make("a")], ["Security"], cfg, day=DAY)

    today_page = tmp_path / "index.html"
    for href in _footer_hrefs(today_page.read_text()):
        target = (today_page.parent / href).resolve()
        assert target.is_file(), f"{href} from {today_page} -> {target}"

    archived_page = tmp_path / "archive" / "2026-08-13.html"
    hrefs = _footer_hrefs(archived_page.read_text())
    assert hrefs, "archived page has no footer links"
    for href in hrefs:
        target = (archived_page.parent / href).resolve()
        assert target.is_file(), f"{href} from {archived_page} -> {target}"


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


ICONS = {"Security": "🔒", "Trending": "📈"}


def test_page_contains_no_em_dash_anywhere():
    """The em-dash is the single most recognisable machine-written tell,
    and it was in the h1, the title, and every undated item's timestamp."""
    html = render_page(
        [make("one"), make("two", category="Trending")], ["Security"], day=DAY, icons=ICONS
    )
    assert "—" not in html
    assert "–" not in html


def test_undated_items_have_no_placeholder_timestamp():
    """An undated article used to print a lone dash where a time would go,
    which reads as a rendering fault rather than as absent information."""
    undated = Article("Trending", "GitHub Trending", "owner/repo",
                      "https://x.test/r", None, blurb="")
    html = render_page([undated], ["Trending"], day=DAY, icons=ICONS)
    assert "owner/repo" in html
    assert ">-<" not in html


def test_category_heading_carries_its_icon_and_count():
    """The digest and the site render the same data; they should not speak
    two different visual languages."""
    html = render_page([make("a"), make("b")], ["Security"], day=DAY, icons=ICONS)
    assert "🔒" in html
    assert ">2<" in html or "2</span>" in html


def test_items_are_grouped_under_their_source():
    html = render_page(
        [make("k1", source="Krebs"), make("b1", source="Bleeping"), make("k2", source="Krebs")],
        ["Security"],
        day=DAY,
        icons=ICONS,
    )
    assert html.count(">Krebs<") == 1
    assert html.count(">Bleeping<") == 1


def test_items_are_not_cards():
    """26 identical elevated cards communicate no hierarchy. Hairline rules
    group without pretending each headline is its own object."""
    html = render_page([make("a"), make("b")], ["Security"], day=DAY, icons=ICONS)
    assert "border-radius" not in html.lower() or "--card" not in html
    assert "box-shadow" not in html.lower()


def test_reduced_motion_is_honoured():
    html = render_page([make("a")], ["Security"], day=DAY, icons=ICONS)
    assert "prefers-reduced-motion" in html


def test_archive_index_has_no_em_dash():
    assert "—" not in render_archive_index([DAY])
