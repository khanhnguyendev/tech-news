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


def test_second_category_split_accounts_for_leftover_room():
    """The item-split budget (`limit - current_len - 2`) is recomputed from
    the live buffer, not a flat `limit - 2`, precisely so a category that
    does NOT fill a chunk still leaves its mark on the budget of whatever
    splits after it. This is the property the header-carry-forward fix
    depends on generally (the header is just the first thing that can
    leave leftover room); with only single-category tests in the suite,
    a future "simplification" back to a flat per-category budget would
    silently break this and go undetected.

    Alpha is small and shares the first chunk with the header. Beta is
    large enough to force a split. If the budget ignored Alpha's (and the
    header's) leftover room, Beta's first sub-block would be sized as if
    it had the whole message to itself and the merge would overflow —
    exactly the class of bug already fixed for the header. Asserting that
    chunk[0] contains articles from *both* categories confirms the budget
    carry-over is genuinely exercised, not incidentally satisfied.
    """
    alpha = [make("H", category="Alpha", link="https://x.test/alpha/%03d" % i) for i in range(5)]
    beta = [make("H", category="Beta", link="https://x.test/beta/%03d" % i) for i in range(100)]
    articles = alpha + beta

    chunks = render_digest(articles, ["Alpha", "Beta"], day=DAY)

    assert len(chunks) > 1
    first_categories = {"alpha" if "/alpha/" in aid else "beta" for aid in chunks[0].article_ids}
    assert first_categories == {"alpha", "beta"}

    assert all(len(c.html) <= MAX_MESSAGE_CHARS for c in chunks)
    delivered = [aid for c in chunks for aid in c.article_ids]
    assert sorted(delivered) == sorted(a.id for a in articles)
    assert len(delivered) == len(set(delivered))


# A fixed table of size combinations, deterministic (no randomness, no
# hash-derived lengths) so it can never flake. This is a compact, persisted
# stand-in for the ad hoc randomized trials used to validate the splitting
# algorithm during development: across every shape below -- no split, one
# category forced to split, several categories of mixed size, a single
# article -- every chunk must respect the limit and every article must be
# delivered exactly once.
SIZE_TABLE = [
    {"Alpha": 1},
    {"Alpha": 3, "Beta": 3},
    {"Security": 120},
    {"Alpha": 5, "Beta": 100},
    {"Alpha": 40, "Beta": 40, "Gamma": 40},
    {"Alpha": 200, "Beta": 1, "Gamma": 50},
]


def test_failure_footer_appears_on_last_chunk_when_sources_failed():
    articles = [make("one"), make("two")]
    [chunk] = render_digest(articles, ["Security"], day=DAY, failed_count=2)
    assert "2 sources failed" in chunk.html
    assert "app.log" in chunk.html


def test_failure_footer_uses_singular_for_one_source():
    [chunk] = render_digest([make("one")], ["Security"], day=DAY, failed_count=1)
    assert "1 source failed" in chunk.html


def test_no_failure_footer_when_nothing_failed():
    [chunk] = render_digest([make("one")], ["Security"], day=DAY, failed_count=0)
    assert "failed" not in chunk.html.lower()
    assert "app.log" not in chunk.html


def test_no_failure_footer_when_there_are_no_articles():
    """render_digest([]) must keep returning no chunks at all -- a failure
    footer is never reason enough to send an otherwise-empty digest."""
    assert render_digest([], ["Security"], day=DAY, failed_count=3) == []


def test_failure_footer_is_appended_to_the_last_chunk_when_it_fits():
    articles = [make("Headline", link="https://x.test/only")]
    # 123-char base chunk + "\n\n" + a 45-char footer for failed_count=5 is
    # exactly 170 chars -- fits with room to spare at this limit.
    chunks = render_digest(articles, ["Security"], day=DAY, limit=200, failed_count=5)
    assert len(chunks) == 1
    assert "5 sources failed" in chunks[0].html
    assert len(chunks[0].html) <= 200


def test_failure_footer_gets_its_own_chunk_when_it_would_overflow_the_last_one():
    articles = [make("Headline", link="https://x.test/only")]
    # Same 123-char base chunk, but a limit too tight for the 47-char
    # addition ("\n\n" + the 45-char footer) to land in the same chunk.
    chunks = render_digest(articles, ["Security"], day=DAY, limit=140, failed_count=5)
    assert len(chunks) == 2
    assert "failed" not in chunks[0].html.lower()
    assert "5 sources failed" in chunks[1].html
    assert all(len(c.html) <= 140 for c in chunks)
    # The footer-only chunk carries no article ids: it isn't tied to any
    # specific article and must not affect delivery accounting.
    assert chunks[1].article_ids == []
    assert chunks[0].article_ids == [articles[0].id]


def test_limit_and_ids_hold_across_a_fixed_table_of_category_sizes():
    for sizes in SIZE_TABLE:
        articles = [
            make(
                "Headline %d" % i,
                category=category,
                link="https://x.test/%s/%03d" % (category, i),
            )
            for category, count in sizes.items()
            for i in range(count)
        ]

        chunks = render_digest(articles, list(sizes.keys()), day=DAY)

        assert all(len(c.html) <= MAX_MESSAGE_CHARS for c in chunks), sizes
        delivered = [aid for c in chunks for aid in c.article_ids]
        assert sorted(delivered) == sorted(a.id for a in articles), sizes
        assert len(delivered) == len(set(delivered)), sizes


def test_limit_holds_across_the_size_table_with_a_failure_footer_present():
    """Same table as above, but with failed_count > 0 on every shape: a
    footer landing on a chunk that's already near the limit must never be
    the thing that pushes it over, regardless of how the rest of the
    digest happened to split.
    """
    for sizes in SIZE_TABLE:
        articles = [
            make(
                "Headline %d" % i,
                category=category,
                link="https://x.test/%s/%03d" % (category, i),
            )
            for category, count in sizes.items()
            for i in range(count)
        ]

        chunks = render_digest(articles, list(sizes.keys()), day=DAY, failed_count=3)

        assert all(len(c.html) <= MAX_MESSAGE_CHARS for c in chunks), sizes
        assert "3 sources failed" in chunks[-1].html, sizes
        # Every article must still be delivered exactly once; the footer
        # chunk (if any) contributes no article ids of its own.
        delivered = [aid for c in chunks for aid in c.article_ids]
        assert sorted(delivered) == sorted(a.id for a in articles), sizes
        assert len(delivered) == len(set(delivered)), sizes
