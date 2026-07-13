"""Telegram-safe output formatting (Bot API §Formatting options).

Telegram rejects a `send_message` with `parse_mode="HTML"|"MarkdownV2"` and **400 Bad Request: can't
parse entities** if the text has an unbalanced or illegal entity — e.g. a product model `Galaxy_S24`
sent as Markdown opens an italic run that never closes. A 400 means the customer/shopkeeper gets
NOTHING, so any dynamic value interpolated into a formatted message must be escaped first.

This module provides the guardrail:
- `escape_html` / `to_telegram_html` — build valid Telegram HTML (the robust choice: only `& < >`
  are special, so escaping is total and unambiguous — far fewer footguns than MarkdownV2).
- `escape_markdown_v2` — escape every MarkdownV2 special, for callers that must use Markdown.
- `is_telegram_html_safe` — the validation parser: True iff a string will NOT 400 under HTML mode.

Plain text (no `parse_mode`) is always safe and needs none of this; use these only where a send sets
a `parse_mode`. Today that is the /addproduct confirmation; the LLM reply path stays plain text.
"""

from __future__ import annotations

import re

# Telegram HTML: the only tags the Bot API accepts (subset we use). Anything else 400s.
TELEGRAM_HTML_TAGS = frozenset({"b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
                                "code", "pre", "a"})
# MarkdownV2 reserves these; each must be backslash-escaped when it appears as literal text.
MARKDOWNV2_SPECIALS = r"_*[]()~`>#+-=|{}.!"

_ENTITY = re.compile(r"&(amp|lt|gt|quot|#\d+|#x[0-9a-fA-F]+);")
_TAG = re.compile(r"</?([a-zA-Z0-9]+)(?:\s[^>]*)?>")


def escape_html(s: str) -> str:
    """Escape the three characters Telegram HTML treats as special. Total and unambiguous."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_markdown_v2(s: str) -> str:
    """Backslash-escape every MarkdownV2 special so `s` is rendered as literal text."""
    return re.sub(r"([%s])" % re.escape(MARKDOWNV2_SPECIALS), r"\\\1", s)


# --- Markdown (LLM output) → Telegram HTML -------------------------------------------------
_STRIKE = re.compile(r"~~(.+?)~~")
_FENCE = re.compile(r"```[a-zA-Z0-9]*\n?(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]+)`")
_BULLET = re.compile(r"^[ \t]*[-*+][ \t]+", re.MULTILINE)
_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+(.*?)[ \t]*$", re.MULTILINE)
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC = re.compile(r"\*(?!\s)(.+?)(?<!\s)\*|_(?!\s)(.+?)(?<!\s)_")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def to_telegram_html(text: str) -> str:
    """Convert Markdown-ish LLM output to valid Telegram HTML. Escapes content first, so any
    `& < >` in the text can never be mistaken for markup; only the tags we add are real."""
    if not text:
        return text
    s = escape_html(text)  # escape ALL content up front — structural tags are added below
    s = _FENCE.sub(lambda m: f"<pre>{m.group(1).strip()}</pre>", s)
    s = _INLINE_CODE.sub(r"<code>\1</code>", s)
    s = _BULLET.sub("• ", s)
    s = _HEADING.sub(r"<b>\1</b>", s)
    s = _STRIKE.sub(r"<s>\1</s>", s)
    s = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", s)
    s = _ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", s)
    s = _LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def is_telegram_html_safe(text: str) -> bool:
    """True iff `text` is safe to send with parse_mode='HTML' (won't 400).

    Rules Telegram enforces: every `<...>` is an allowed tag, tags are balanced, and every bare `&`
    and `<` in text is an escaped entity. This is the STAGE-1 validation parser.
    """
    # 1. All tags allowed + balanced (a simple stack; Telegram tags don't self-nest oddly for us).
    stack: list[str] = []
    for m in _TAG.finditer(text):
        raw, name = m.group(0), m.group(1).lower()
        if name not in TELEGRAM_HTML_TAGS:
            return False
        if raw.startswith("</"):
            if not stack or stack.pop() != name:
                return False
        elif not raw.endswith("/>"):
            stack.append(name)
    if stack:
        return False
    # 2. No unescaped '&' (each must open a valid entity) and no stray '<' that isn't a tag start.
    for m in re.finditer(r"&", text):
        if not _ENTITY.match(text, m.start()):
            return False
    # A '<' that _TAG didn't consume is a raw bracket → unsafe.
    consumed = {i for mt in _TAG.finditer(text) for i in range(mt.start(), mt.end())}
    for m in re.finditer(r"<", text):
        if m.start() not in consumed:
            return False
    return True


if __name__ == "__main__":
    assert escape_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"
    assert escape_markdown_v2("Galaxy_S24!") == r"Galaxy\_S24\!"
    # the exact bug: a model name with a markdown special renders safely in HTML
    html = f"Saved {escape_html('Galaxy_S24 <A&B>')} id: <code>uuid-1</code>"
    assert is_telegram_html_safe(html), html
    # conversions produce safe HTML
    for payload in ["**bold**", "_it_", "~~s~~", "`c`", "### H", "- x", "[t](http://u)",
                    "raw < & > chars", "unbalanced ** and _ and `"]:
        out = to_telegram_html(payload)
        assert is_telegram_html_safe(out), (payload, out)
    # validator REJECTS unsafe strings
    assert not is_telegram_html_safe("a < b")            # stray bracket
    assert not is_telegram_html_safe("x & y")            # unescaped ampersand
    assert not is_telegram_html_safe("<b>unclosed")      # unbalanced tag
    assert not is_telegram_html_safe("<script>x</script>")  # illegal tag
    assert is_telegram_html_safe("<b>ok</b> &amp; <i>fine</i>")
    print("telegram format self-check ok")
