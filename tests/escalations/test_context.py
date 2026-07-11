"""Redis conversation memory (SPEC §3 handover context, §7 last-25, §11 zero local memory)."""

from __future__ import annotations

from uuid import uuid4

import fakeredis.aioredis
import pytest

from app.escalations.context import SESSION_MAX, forget, history, remember, session_key

SHOP_A, SHOP_B = uuid4(), uuid4()


@pytest.fixture
def redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.mark.asyncio
async def test_turns_come_back_oldest_first(redis):
    await remember(redis, SHOP_A, "p1", "customer", "hello")
    await remember(redis, SHOP_A, "p1", "assistant", "hi!")
    assert await history(redis, SHOP_A, "p1") == [
        {"role": "customer", "content": "hello"},
        {"role": "assistant", "content": "hi!"},
    ]


@pytest.mark.asyncio
async def test_empty_conversation_is_not_an_error(redis):
    assert await history(redis, SHOP_A, "nobody") == []


@pytest.mark.asyncio
async def test_only_the_last_25_are_kept(redis):
    """SPEC §7 snapshots the last 25 messages into security_incidents."""
    for i in range(40):
        await remember(redis, SHOP_A, "p1", "customer", f"msg{i}")
    turns = await history(redis, SHOP_A, "p1")
    assert len(turns) == SESSION_MAX
    assert turns[0]["content"] == "msg15" and turns[-1]["content"] == "msg39"


@pytest.mark.asyncio
async def test_sessions_are_scoped_per_shop(redis):
    await remember(redis, SHOP_A, "p1", "customer", "at shop A")
    assert await history(redis, SHOP_B, "p1") == []


@pytest.mark.asyncio
async def test_session_expires_so_a_stale_conversation_becomes_a_new_one(redis):
    await remember(redis, SHOP_A, "p1", "customer", "hello")
    assert await redis.ttl(session_key(SHOP_A, "p1")) > 0


@pytest.mark.asyncio
async def test_a_poisoned_entry_is_dropped_not_fatal(redis):
    await remember(redis, SHOP_A, "p1", "customer", "good")
    await redis.rpush(session_key(SHOP_A, "p1"), "{not json")
    assert await history(redis, SHOP_A, "p1") == [{"role": "customer", "content": "good"}]


@pytest.mark.asyncio
async def test_forget_clears_the_conversation(redis):
    await remember(redis, SHOP_A, "p1", "customer", "hello")
    await forget(redis, SHOP_A, "p1")
    assert await history(redis, SHOP_A, "p1") == []
