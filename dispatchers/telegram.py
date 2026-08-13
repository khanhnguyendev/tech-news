"""Telegram digest rendering.

Sending lives in the same module (added in Task 11) but rendering is kept
pure: it takes articles and returns message-sized chunks, each carrying the
article ids it contains so delivery can be accounted for per message.
"""

from __future__ import annotations

import html as html_module
from dataclasses import dataclass, field
from datetime import date

from models import Article

MAX_MESSAGE_CHARS = 4096


@dataclass
class Chunk:
    html: str
    article_ids: list[str] = field(default_factory=list)


def escape(text: str) -> str:
    """Escape the three characters Telegram's HTML parse mode reserves."""
    return html_module.escape(text, quote=False)


def _render_item(article: Article, include_blurb: bool) -> str:
    line = (
        f'• <a href="{escape(article.link)}">{escape(article.headline)}</a>'
        f" — <i>{escape(article.source)}</i>"
    )
    if include_blurb and article.blurb:
        line += f"\n  {escape(article.blurb)}"
    return line


def _ordered_categories(articles: list[Article], category_order: list[str]) -> list[str]:
    present = {a.category for a in articles}
    ordered = [c for c in category_order if c in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def render_digest(
    articles: list[Article],
    category_order: list[str],
    *,
    day: date,
    include_blurb: bool = False,
    limit: int = MAX_MESSAGE_CHARS,
) -> list[Chunk]:
    """Render the digest into chunks that each fit one Telegram message.

    Splits at category boundaries, then at item boundaries when a single
    category is too large. Never splits inside a tag.
    """
    if not articles:
        return []

    header = (
        f"<b>TechNews — {day.strftime('%d %b %Y')}</b>\n"
        f"<i>{len(articles)} stories</i>"
    )

    chunks: list[Chunk] = []
    current_lines: list[str] = [header]
    current_ids: list[str] = []
    current_len = len(header)

    def flush() -> None:
        nonlocal current_lines, current_ids, current_len
        if current_ids:
            chunks.append(Chunk("\n\n".join(current_lines), current_ids))
            current_lines = []
            current_ids = []
            current_len = 0
        # Otherwise the buffer holds only the pending header (no ids yet):
        # carry it forward into the next chunk instead of discarding it,
        # so the date/story-count line always survives -- even when the
        # very first category is already too large to share a message
        # with it.

    def add_block(block: str, ids: list[str]) -> None:
        nonlocal current_len
        # +2 assumes a "\n\n" separator precedes this block. That's true
        # whenever current_lines already has content, but also charged for
        # the first block right after a flush (where no separator is
        # actually joined in). current_len therefore runs up to 2 chars
        # higher than len("\n\n".join(current_lines)) in that case -- a
        # conservative overestimate that never causes overflow, just a
        # slightly earlier flush than strictly necessary.
        addition = len(block) + 2
        if current_lines and current_len + addition > limit:
            flush()
        current_lines.append(block)
        current_ids.extend(ids)
        current_len += addition

    for category in _ordered_categories(articles, category_order):
        in_category = [a for a in articles if a.category == category]
        heading = f"<b>{escape(category)}</b>"
        items = [_render_item(a, include_blurb) for a in in_category]

        # Always split at item boundaries. The budget for each sub-block is
        # recomputed from the live buffer state (current_len) right before
        # building it, so a pending header -- or leftover room left by the
        # previous category -- is respected instead of blown through. For a
        # category that comfortably fits, this loop runs exactly once and
        # behaves like the old "add the whole category" fast path.
        index = 0
        continued = False
        while index < len(in_category):
            label = heading if not continued else f"{heading} <i>(cont.)</i>"
            budget = max(limit - current_len - 2, len(label) + 1)
            sub_lines = [label]
            sub_ids: list[str] = []
            sub_len = len(label)
            while index < len(in_category):
                item = items[index]
                addition = len(item) + 1
                # The first item of a sub-block is always let in regardless
                # of budget, to guarantee progress. If a single rendered
                # item alone is longer than the limit (an ~4000-char
                # headline+link), its chunk will exceed MAX_MESSAGE_CHARS.
                # Inherited from the reference design; not worth guarding
                # against for a headline digest.
                if sub_ids and sub_len + addition > budget:
                    break
                sub_lines.append(item)
                sub_ids.append(in_category[index].id)
                sub_len += addition
                index += 1
            add_block("\n".join(sub_lines), sub_ids)
            continued = True

    flush()
    return chunks
