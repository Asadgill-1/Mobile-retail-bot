"""AUDIT CATEGORY 2 — Webhook latency & deduplication (Meta/Twilio retry storm).

Fire 3 identical inbound messages with the SAME message_sid concurrently (asyncio.gather), as a
provider retry storm does when the LLM is slow to ack. The pipeline's §11 layer (per-session lock
+ SET NX dedup on the sid) must let exactly ONE run reach the LLM; the other two return instantly
with a handled action and no LLM call.

Real code under test: messaging.pipeline.process_message. fakeredis gives real atomic SET NX
semantics, so the concurrency guard is exercised for real, not mocked.

Pass: LLM invoked exactly once; the other two actions are handled ('duplicate' or 'locked'), and
every one of the 3 requests returns (a quick HTTP 200 in prod terms).
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import fakeredis.aioredis
import pytest

import app.messaging.pipeline as pipeline
from app.messaging.pipeline import InboundMessage, process_message
from app.tenants.models import Shop


@pytest.fixture
def redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def count_llm(monkeypatch) -> dict:
    """Count LLM executions; each call is slow (yields) so the 3 requests truly overlap."""
    calls = {"n": 0}

    async def _answer(shop, identity, text, redis, media_sink=None):
        calls["n"] += 1
        await asyncio.sleep(0.05)  # simulate a slow LLM completion loop → provider retries fire
        return f"AI reply for {shop.name}"

    monkeypatch.setattr(pipeline, "answer_customer", _answer)
    return calls


@pytest.mark.asyncio
async def test_retry_storm_triggers_one_llm_run(redis, count_llm):
    shop = Shop(id=uuid4(), client_id=uuid4(), name="Shop 01")
    sid = "wamid.RETRY_STORM_ID"  # identical provider message id on all 3 deliveries

    async def _fire():
        return await process_message(
            InboundMessage(shop, "cust-1", "hello", message_sid=sid), redis
        )

    results = await asyncio.gather(_fire(), _fire(), _fire())

    # Exactly one LLM completion loop, despite 3 concurrent identical webhooks.
    assert count_llm["n"] == 1
    # All three requests returned (prod: quick HTTP 200 each).
    assert len(results) == 3
    actions = sorted(r.action for r in results)
    # One did the AI work; the other two were caught by the dedup/lock guard.
    assert "ai" in actions
    handled = [a for a in actions if a != "ai"]
    assert len(handled) == 2
    assert all(a in ("duplicate", "locked") for a in handled)
    # The deduped/locked responses carry no reply body — nothing re-sent to the customer.
    assert all(r.reply is None for r in results if r.action != "ai")


@pytest.mark.asyncio
async def test_sequential_redelivery_is_dropped_after_first(redis, count_llm):
    """A retry that arrives AFTER the first finished still dedupes on the sid (5-min window)."""
    shop = Shop(id=uuid4(), client_id=uuid4(), name="Shop 01")
    sid = "wamid.SEQUENTIAL"

    first = await process_message(InboundMessage(shop, "c", "hi", message_sid=sid), redis)
    second = await process_message(InboundMessage(shop, "c", "hi", message_sid=sid), redis)

    assert first.action == "ai"
    assert second.action == "duplicate" and second.reply is None
    assert count_llm["n"] == 1  # the redelivery never reached the LLM
