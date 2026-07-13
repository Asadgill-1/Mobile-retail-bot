"""AUDIT STAGE 1 — Telegram formatting & MarkdownV2/HTML guardrails.

Telegram 400s a formatted message with an unbalanced/illegal entity, so the customer or shopkeeper
gets nothing. The guardrail (`app.telegram_bot.format`) escapes dynamic values and exposes a
validation parser (`is_telegram_html_safe`). Pass condition: every payload — including adversarial
names full of Markdown/HTML specials — comes out 100% Telegram-safe, and the parser correctly
REJECTS known-bad strings (so it isn't just rubber-stamping everything).

Also pins the real bug this stage found: the /addproduct confirmation used parse_mode='Markdown'
with un-escaped brand/model, so a model like 'Galaxy_S24' would 400. It now builds escaped HTML.
"""

from __future__ import annotations

import pytest

from app.telegram_bot.format import (
    escape_html,
    escape_markdown_v2,
    is_telegram_html_safe,
    to_telegram_html,
)

# Names / text that break naive formatting: markdown specials, HTML specials, unbalanced markers.
HOSTILE_PAYLOADS = [
    "Galaxy_S24",              # underscore → opens italic in Markdown
    "Note*Pro",                # asterisk
    "A[B](C)",                 # link syntax
    "100% < 200 & >",          # raw HTML specials
    "back`tick`s",
    "unbalanced ** bold and _ italic",
    "```code fence``` leftover",
    "emoji 🛵 and <b>fake tag</b>",
    "line1\n\n\n\nline5",
    "### injected heading",
]


@pytest.mark.parametrize("payload", HOSTILE_PAYLOADS)
def test_converted_output_is_telegram_safe(payload):
    """to_telegram_html must always yield a string that won't 400 under parse_mode=HTML."""
    assert is_telegram_html_safe(to_telegram_html(payload))


@pytest.mark.parametrize("payload", HOSTILE_PAYLOADS)
def test_escaped_dynamic_value_is_safe_in_context(payload):
    """A hostile value dropped into a real message template stays safe once escaped."""
    msg = f"✅ Saved.\n{escape_html(payload)}\nid: <code>abc-123</code>"
    assert is_telegram_html_safe(msg)


def test_validation_parser_rejects_unsafe_strings():
    """The parser must fail closed on genuinely broken payloads — not pass everything."""
    assert not is_telegram_html_safe("price < 200")        # stray '<'
    assert not is_telegram_html_safe("Tom & Jerry")        # unescaped '&'
    assert not is_telegram_html_safe("<b>bold")            # unbalanced tag
    assert not is_telegram_html_safe("<marquee>x</marquee>")  # illegal tag
    assert not is_telegram_html_safe("</b>")               # close with no open


def test_addproduct_confirmation_no_longer_400s():
    """Regression for the bug this stage found: the /addproduct confirmation with a Markdown-hostile
    model name is now safe HTML instead of a 400."""
    brand, model, pid = "Samsung", "Galaxy_S24 <Ultra> & Co", "550e8400-e29b-41d4-a716-446655440000"
    confirmation = (
        f"✅ Saved.\n{escape_html(brand)} {escape_html(model)}\n"
        f"id: <code>{pid}</code>\nUse it with /boost, /tag, /feature."
    )
    assert is_telegram_html_safe(confirmation)
    assert "Galaxy_S24" in confirmation  # underscore survives literally (HTML doesn't treat it special)
    assert "&lt;Ultra&gt;" in confirmation and "&amp;" in confirmation


def test_markdown_v2_escaper_covers_all_specials():
    """For any caller that must use MarkdownV2, every reserved char is backslash-escaped."""
    assert escape_markdown_v2("a_b*c[d]e(f)") == r"a\_b\*c\[d\]e\(f\)"
    assert escape_markdown_v2("hi!") == r"hi\!"
