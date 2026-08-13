from datetime import date, datetime, timezone

from dispatchers.telegram import MAX_MESSAGE_CHARS, escape, render_digest
from models import Article

UTC = timezone.utc
DAY = date(2026, 8, 13)


def make(headline, category="Security", source="Krebs", link=None, blurb=""):
    return Article(
        category=category,
        source=source,
        headline=headline,
        link=link or f"https://x.test/{abs(hash(headline))}",
        published=datetime(2026, 8, 13, tzinfo=UTC),
        blurb=blurb,
    )


def test_escape_handles_telegram_special_characters():
    assert escape("A & B <script> C > D") == "A &amp; B &lt;script&gt; C &gt; D"


def test_escape_leaves_quotes_alone():
    """Quotes are not escaped: they are legal in Telegram HTML text and
    escaping them makes headlines read badly."""
    assert escape('He said "hi"') == 'He said "hi"'


def test_rendered_output_is_escaped_exactly_once():
    [chunk] = render_digest([make("A & B")], ["Security"], day=DAY)
    assert "A &amp; B" in chunk.html
    assert "&amp;amp;" not in chunk.html


def test_headline_with_markup_is_escaped_in_output():
    [chunk] = render_digest([make("Bug in <iframe> & <script>")], ["Security"], day=DAY)
    assert "&lt;iframe&gt;" in chunk.html
    assert "<iframe>" not in chunk.html


def test_groups_by_category_in_given_order():
    articles = [make("s1", category="Security"), make("a1", category="Anthropic")]
    [chunk] = render_digest(articles, ["Anthropic", "Security"], day=DAY)
    assert chunk.html.index("Anthropic") < chunk.html.index("Security")


def test_includes_header_with_date_and_count():
    [chunk] = render_digest([make("one"), make("two")], ["Security"], day=DAY)
    assert "13 Aug 2026" in chunk.html
    assert "2 stories" in chunk.html


def test_renders_link_and_source_per_item():
    [chunk] = render_digest(
        [make("Headline", link="https://k.test/p", source="Krebs")], ["Security"], day=DAY
    )
    assert '<a href="https://k.test/p">Headline</a>' in chunk.html
    assert "Krebs" in chunk.html


def test_blurb_omitted_by_default_and_included_when_asked():
    article = make("Headline", blurb="Some detail here")
    [without] = render_digest([article], ["Security"], day=DAY)
    [with_blurb] = render_digest(
        [article], ["Security"], day=DAY, include_blurb=True
    )
    assert "Some detail here" not in without.html
    assert "Some detail here" in with_blurb.html


def test_chunk_records_the_ids_it_contains():
    articles = [make("one", link="https://x.test/1"), make("two", link="https://x.test/2")]
    [chunk] = render_digest(articles, ["Security"], day=DAY)
    assert set(chunk.article_ids) == {"https://x.test/1", "https://x.test/2"}


def test_every_chunk_respects_the_character_limit():
    articles = [
        make("Headline number %d with some length to it" % i, category="Security")
        for i in range(120)
    ]
    chunks = render_digest(articles, ["Security"], day=DAY)
    assert len(chunks) > 1
    assert all(len(c.html) <= MAX_MESSAGE_CHARS for c in chunks)


def test_split_preserves_every_article_exactly_once():
    articles = [make("Headline %d" % i, link="https://x.test/%d" % i) for i in range(120)]
    chunks = render_digest(articles, ["Security"], day=DAY)
    delivered = [aid for c in chunks for aid in c.article_ids]
    assert sorted(delivered) == sorted(a.id for a in articles)
    assert len(delivered) == len(set(delivered))


def test_split_never_cuts_inside_a_tag():
    articles = [make("Headline %d" % i) for i in range(120)]
    for chunk in render_digest(articles, ["Security"], day=DAY):
        assert chunk.html.count("<a href=") == chunk.html.count("</a>")
        assert chunk.html.count("<b>") == chunk.html.count("</b>")


def test_no_articles_produces_no_chunks():
    assert render_digest([], ["Security"], day=DAY) == []


def test_categories_absent_from_order_still_appear_last():
    articles = [make("x", category="Surprise")]
    [chunk] = render_digest(articles, ["Security"], day=DAY)
    assert "Surprise" in chunk.html


def test_header_survives_when_the_first_category_forces_a_split():
    """Regression test for a defect in the original flush(): when the very
    first category is already too large to share a message with the
    header, flush() was called while current_ids was still empty, and the
    unconditional buffer reset discarded the header (date + story count)
    entirely. The digest would then go out with no date and no story
    count on exactly the high-volume days when readers need them most.

    flush() must carry a pending header-only buffer forward into the next
    chunk instead of discarding it, so the header appears exactly once,
    in the first chunk.

    Article lengths here are fixed (not hash-derived like `make`'s default
    link) so the boundary math is deterministic: each rendered item is
    short enough, relative to the header, to guarantee the first pending
    sub-block lands close enough to the 4096 limit that appending it on
    top of the header overflows -- reliably forcing the defect path on
    every run, regardless of Python's per-process hash randomization.
    """
    articles = [
        make("H", link="https://x.test/%03d" % i) for i in range(100)
    ]
    chunks = render_digest(articles, ["Security"], day=DAY)
    assert len(chunks) > 1
    assert "13 Aug 2026" in chunks[0].html
    assert sum("13 Aug 2026" in c.html for c in chunks) == 1
