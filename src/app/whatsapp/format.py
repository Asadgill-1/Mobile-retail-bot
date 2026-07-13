"""Markdown → WhatsApp text formatter (SPEC §13 outbound; ADR-002 Stage-13 cutover).

The LLM answers in Markdown (`**bold**`, `### Heading`, `- bullet`, `` `code` ``). WhatsApp does
NOT render Markdown — it has its own tiny set: `*bold*`, `_italic_`, `~strike~`, and ``` ```mono``` ```.
Sending raw Markdown to WhatsApp shows the literal `**` and `###` to the customer. This scrubs every
Markdown marker, converting it to the WhatsApp-native equivalent or removing it.

Used on the WhatsApp outbound path only (Stage 13). The Telegram test channel sends plain text, so
this is not wired into that path. `to_whatsapp` is pure — one text in, cleaned text out.

ponytail: regex pass over the documented Markdown subset the model actually emits (headings, bold,
italic, strike, code, bullets, links). ceiling: not a full CommonMark parser — nested/edge Markdown
may slip through. upgrade: swap in a real md parser if the model starts emitting richer Markup.
"""

from __future__ import annotations

import re

_BOLD = "\x01"  # private placeholder: protect bold/heading spans from the italic pass, restored last

_STRIKE = re.compile(r"~~(.+?)~~")
_CODE_FENCE = re.compile(r"```[a-zA-Z0-9]*\n?(.*?)```", re.DOTALL)  # ```lang\n…``` → keep the code
_CODE_INLINE = re.compile(r"`([^`]+)`")                            # `code` → code (drop the ticks)
_BULLET = re.compile(r"^[ \t]*[-*+][ \t]+", re.MULTILINE)          # -, *, + list markers → •
_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+(.*?)[ \t]*$", re.MULTILINE)
_BOLD_STARS = re.compile(r"\*\*(.+?)\*\*")
_BOLD_UNDERS = re.compile(r"__(.+?)__")
_ITALIC_STAR = re.compile(r"\*(?!\s)(.+?)(?<!\s)\*")               # single *italic* → _italic_
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")                     # [text](url) → text (url)
_BLOCKQUOTE = re.compile(r"^[ \t]*>[ \t]?", re.MULTILINE)          # > quote → (strip marker)
_NEWLINES = re.compile(r"\n{3,}")                                  # collapse 3+ blank lines → one gap


def to_whatsapp(text: str) -> str:
    """Convert Markdown-ish LLM output to WhatsApp-native formatting. Never raises."""
    if not text:
        return text
    s = text
    s = _STRIKE.sub(r"~\1~", s)                       # ~~x~~ → ~x~
    s = _CODE_FENCE.sub(lambda m: m.group(1).strip(), s)  # drop code fences, keep contents
    s = _CODE_INLINE.sub(r"\1", s)                    # `x` → x
    s = _BLOCKQUOTE.sub("", s)                        # strip > markers
    s = _BULLET.sub("• ", s)                          # -, *, + bullets → •  (before italic pass)
    s = _HEADING.sub(_BOLD + r"\1" + _BOLD, s)        # ### H → «bold»H«bold» (protected)
    s = _BOLD_STARS.sub(_BOLD + r"\1" + _BOLD, s)     # **x** → protected
    s = _BOLD_UNDERS.sub(_BOLD + r"\1" + _BOLD, s)    # __x__ → protected
    s = _ITALIC_STAR.sub(r"_\1_", s)                  # *x* → _x_   (bold already protected)
    s = s.replace(_BOLD, "*")                         # restore bold/headings → *x*
    s = _LINK.sub(r"\1 (\2)", s)                      # [t](u) → t (u)
    s = _NEWLINES.sub("\n\n", s)                      # keep at most a double line-break
    return s.strip()


if __name__ == "__main__":
    out = to_whatsapp(
        "### Weekly Deals\n\n"
        "Here are our **best** phones and _top_ picks:\n"
        "- Redmi Note 13 — `AED 899`\n"
        "* Galaxy A55 — ~~1500~~ 1299\n\n\n"
        "See [our catalogue](https://shop.example/cat)."
    )
    assert "#" not in out, out
    assert "`" not in out, out
    assert "**" not in out, out
    assert "~~" not in out, out
    assert "*Weekly Deals*" in out          # heading → bold
    assert "*best*" in out                  # **bold** → *bold*
    assert "_top_" in out                   # _italic_ preserved
    assert "~1500~" in out                  # ~~strike~~ → ~strike~
    assert "• Redmi" in out and "• Galaxy" in out   # bullets normalised
    assert "our catalogue (https://shop.example/cat)" in out
    assert "\n\n\n" not in out              # collapsed blank lines
    print("whatsapp format self-check ok")
