"""AUDIT CATEGORY 4 — WhatsApp-compliant output sanitization.

The LLM answers in Markdown. WhatsApp does not render Markdown, so `whatsapp.format.to_whatsapp`
must scrub every Markdown marker, leaving only WhatsApp-native formatting (`*bold*`, `_italic_`,
`~strike~`, `• bullets`, plain links, double line-breaks).

Pass: no raw Markdown markers survive (`#`, `` ` ``, `**`, `~~`, `[..](..)`, leading `- `), and each
construct is transformed to its WhatsApp equivalent.
"""

from __future__ import annotations

import pytest

from app.whatsapp.format import to_whatsapp

_MARKDOWN_SAMPLE = (
    "# Big Sale\n"
    "## Phones\n\n"
    "Check our **best deals** and _limited_ stock:\n"
    "- Redmi Note 13 for `AED 899`\n"
    "- Galaxy A55 was ~~1500~~ now 1299\n"
    "+ Free `case` included\n\n\n"
    "More at [our store](https://shop.example/deals).\n"
    "> hurry while stocks last"
)


def test_no_raw_markdown_markers_survive():
    out = to_whatsapp(_MARKDOWN_SAMPLE)
    assert "#" not in out            # headings gone
    assert "`" not in out            # code ticks gone
    assert "**" not in out           # md bold gone
    assert "~~" not in out           # md strike gone
    assert "](" not in out           # md link syntax gone
    for line in out.splitlines():
        assert not line.lstrip().startswith(("- ", "* ", "+ ")), line  # md bullets gone


def test_transforms_to_whatsapp_native():
    out = to_whatsapp(_MARKDOWN_SAMPLE)
    assert "*Big Sale*" in out                        # heading → bold
    assert "*Phones*" in out
    assert "*best deals*" in out                      # **x** → *x*
    assert "_limited_" in out                         # italic preserved
    assert "~1500~" in out                            # ~~x~~ → ~x~
    assert "• Redmi Note 13" in out                   # bullet normalised
    assert "• Galaxy A55" in out
    assert "• Free case included" in out              # bullet + inline code stripped
    assert "our store (https://shop.example/deals)" in out  # link flattened
    assert "hurry while stocks last" in out           # blockquote marker stripped
    assert ">" not in out


def test_double_line_breaks_preserved_not_tripled():
    out = to_whatsapp(_MARKDOWN_SAMPLE)
    assert "\n\n" in out           # paragraph breaks kept
    assert "\n\n\n" not in out     # never more than a double break


@pytest.mark.parametrize("raw,expected", [
    ("**bold**", "*bold*"),
    ("__also bold__", "*also bold*"),
    ("*italic*", "_italic_"),
    ("~~strike~~", "~strike~"),
    ("`code`", "code"),
    ("### Heading", "*Heading*"),
    ("- item", "• item"),
    ("[t](u)", "t (u)"),
])
def test_single_construct_conversions(raw, expected):
    assert to_whatsapp(raw) == expected


def test_empty_and_plain_text_untouched():
    assert to_whatsapp("") == ""
    assert to_whatsapp("just a normal sentence.") == "just a normal sentence."
