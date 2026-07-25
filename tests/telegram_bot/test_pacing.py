"""Pacer: coalescing, bubble splitting, and the interruption rules.

The rule that matters most: a customer who fires off a new question mid-answer must never lose a
message. Everything else here is cosmetic by comparison.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.telegram_bot.pacing import Pacer, split_bubbles, strip_markdown, typing_delay


# --- formatting the sender has to undo (the model slips; there is no parse_mode) ---
def test_strip_markdown_removes_what_no_human_types():
    raw = "## Deal\n**Galaxy S25** is 3400 AED\n- 200MP camera\n* big battery\nprice — final"
    out = strip_markdown(raw)
    assert "**" not in out and "##" not in out
    assert "Galaxy S25 is 3400 AED" in out
    assert out.count("—") == 0  # em-dash is an AI tell
    assert "200MP camera" in out and "- 200MP" not in out


def test_split_bubbles_uses_blank_lines():
    assert split_bubbles("first\n\nsecond") == ["first", "second"]


def test_split_bubbles_keeps_a_single_short_reply_whole():
    assert split_bubbles("we have it, 3400 AED") == ["we have it, 3400 AED"]


def test_split_bubbles_merges_the_tail_rather_than_dropping_it():
    """Losing half an answer to a formatting rule would be far worse than one long message."""
    out = split_bubbles("a\n\nb\n\nc\n\nd\n\ne", limit=3)
    assert len(out) == 3
    assert out[:2] == ["a", "b"]
    assert "c" in out[2] and "d" in out[2] and "e" in out[2]


def test_split_bubbles_of_nothing_sends_nothing():
    assert split_bubbles("") == [] and split_bubbles(None or "") == []


def test_typing_delay_is_bounded_both_ways():
    assert typing_delay("hi") >= 0.6
    assert typing_delay("x" * 10_000) <= 3.5
    assert typing_delay("x" * 50) > typing_delay("hi")  # longer message, longer pause


# --- coalescing + interruption ---
def _wire(reply="ok", answer_delay=0.0):
    """A fake conversation: records the batches answered and the bubbles sent."""
    state = {"batches": [], "sent": [], "typing": 0}

    async def answer(text):
        state["batches"].append(text)
        if answer_delay:
            await asyncio.sleep(answer_delay)
        return SimpleNamespace(reply=reply, media=())

    async def send(text):
        state["sent"].append(text)

    async def typing():
        state["typing"] += 1

    return state, answer, send, typing


@pytest.mark.asyncio
async def test_rapid_fragments_become_one_answer():
    """'hi' / 'want iphone' / 'budget 3000' is one thought, not three questions."""
    state, answer, send, typing = _wire()
    pacer = Pacer(debounce=0.05)
    shop = uuid4()

    for text in ("hi", "want iphone", "budget 3000"):
        task = pacer.submit(shop, "p1", text, answer=answer, send=send, typing=typing)
        await asyncio.sleep(0)  # let the worker start, but stay inside the debounce window
    await task

    assert state["batches"] == ["hi\nwant iphone\nbudget 3000"], "one AI call, not three"
    assert state["sent"] == ["ok"]


@pytest.mark.asyncio
async def test_a_message_arriving_mid_answer_is_answered_next_not_dropped():
    """The paid-for answer lands first, then the follow-up. Nothing is lost."""
    state, answer, send, typing = _wire(answer_delay=0.1)
    pacer = Pacer(debounce=0.02)
    shop = uuid4()

    task = pacer.submit(shop, "p1", "how much", answer=answer, send=send, typing=typing)
    await asyncio.sleep(0.08)  # debounce elapsed; now inside the LLM call
    pacer.submit(shop, "p1", "and delivery?", answer=answer, send=send, typing=typing)
    await task

    assert state["batches"] == ["how much", "and delivery?"]
    assert len(state["sent"]) == 2


@pytest.mark.asyncio
async def test_separate_customers_do_not_share_a_worker():
    state, answer, send, typing = _wire()
    pacer = Pacer(debounce=0.02)
    shop = uuid4()

    t1 = pacer.submit(shop, "p1", "first", answer=answer, send=send, typing=typing)
    t2 = pacer.submit(shop, "p2", "second", answer=answer, send=send, typing=typing)
    await asyncio.gather(t1, t2)

    assert sorted(state["batches"]) == ["first", "second"]


@pytest.mark.asyncio
async def test_typing_indicator_fires_once_per_bubble():
    state, answer, send, typing = _wire(reply="line one\n\nline two")
    pacer = Pacer(debounce=0.01)

    await pacer.submit(uuid4(), "p1", "hi", answer=answer, send=send, typing=typing)

    assert state["sent"] == ["line one", "line two"]
    assert state["typing"] == 2


@pytest.mark.asyncio
async def test_a_failing_answer_does_not_wedge_the_conversation():
    """The next message must still get a worker."""
    calls = {"n": 0}

    async def answer(text):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("provider down")
        return SimpleNamespace(reply="recovered", media=())

    sent = []

    async def send(text):
        sent.append(text)

    pacer = Pacer(debounce=0.01)
    shop = uuid4()

    await pacer.submit(shop, "p1", "hi", answer=answer, send=send)
    await pacer.submit(shop, "p1", "still there?", answer=answer, send=send)

    assert calls["n"] == 2 and sent == ["recovered"]
