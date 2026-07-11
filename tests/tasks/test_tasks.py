"""Celery task core (`_run`) — the dormant Twilio path's one runnable check (ADR-002)."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import fakeredis.aioredis
import pytest

import app.messaging.pipeline as pipeline
from app.db.in_memory import InMemoryTenantRepo
from app.messaging.pipeline import _USAGE_KEY, parse_usage_key
from app.tasks.tasks import _run, flush_usage


def _repo() -> InMemoryTenantRepo:
    repo = InMemoryTenantRepo()
    repo.seed_default()
    return repo


@pytest.fixture(autouse=True)
def stub_ai(monkeypatch):
    """`_run` reaches pipeline step 7 → the real AI service. Mock it: no network in unit tests."""

    async def _answer(shop, identity, text, redis, media_sink=None):
        return "stubbed AI reply"

    monkeypatch.setattr(pipeline, "answer_customer", _answer)


@pytest.mark.asyncio
async def test_run_executes_pipeline_for_known_shop():
    repo = _repo()
    active = next(s for s in await repo.list_shops() if s.status.value == "active")
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    action = await _run(repo, redis, str(active.id), "+199", "hi", "SM1")
    assert action == "ai"


@pytest.mark.asyncio
async def test_run_returns_unknown_shop_for_missing_id():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    action = await _run(_repo(), redis, str(uuid4()), "+199", "hi", None)
    assert action == "unknown_shop"


# --- daily usage flush (ADR-006) ---
_TODAY = date(2026, 7, 10)


def _key(cid, sid, day: str, metric: str = "messages") -> str:
    return _USAGE_KEY.format(client_id=cid, shop_id=sid, day=day, metric=metric)


async def _seed_repo_and_ids():
    repo = _repo()
    shop = (await repo.list_shops())[0]
    return repo, shop.client_id, shop.id


@pytest.mark.asyncio
async def test_flush_drains_completed_day_but_leaves_today():
    repo, cid, sid = await _seed_repo_and_ids()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await redis.set(_key(cid, sid, "2026-07-09"), "5")  # yesterday — complete
    await redis.set(_key(cid, sid, "2026-07-10"), "3")  # today — still accumulating

    flushed = await flush_usage(repo, redis, today=_TODAY)

    assert flushed == 1
    rows = {(r.metric): r.count for r in await repo.get_usage(cid, date(2026, 7, 9))}
    assert rows == {"messages": 5}                       # yesterday persisted
    assert await redis.get(_key(cid, sid, "2026-07-09")) is None   # drained key deleted
    assert await redis.get(_key(cid, sid, "2026-07-10")) == "3"    # today untouched


@pytest.mark.asyncio
async def test_flush_is_idempotent_no_double_count():
    repo, cid, sid = await _seed_repo_and_ids()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await redis.set(_key(cid, sid, "2026-07-09"), "5")

    assert await flush_usage(repo, redis, today=_TODAY) == 1
    assert await flush_usage(repo, redis, today=_TODAY) == 0  # nothing left → no overwrite
    assert [r.count for r in await repo.get_usage(cid, date(2026, 7, 9))] == [5]


@pytest.mark.asyncio
async def test_flush_skips_malformed_key():
    repo, cid, sid = await _seed_repo_and_ids()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await redis.set("usage:garbage", "9")               # malformed — must be left alone
    await redis.set(_key(cid, sid, "2026-07-09"), "5")

    assert await flush_usage(repo, redis, today=_TODAY) == 1
    assert await redis.get("usage:garbage") == "9"       # untouched, not drained


def test_parse_usage_key_roundtrip_and_rejects_junk():
    cid, sid = uuid4(), uuid4()
    key = _USAGE_KEY.format(client_id=cid, shop_id=sid, day="2026-07-09", metric="messages")
    assert parse_usage_key(key) == (cid, sid, date(2026, 7, 9), "messages")
    assert parse_usage_key("usage:garbage") is None          # wrong field count
    assert parse_usage_key(f"usage:not-a-uuid:{sid}:2026-07-09:messages") is None
    assert parse_usage_key(f"usage:{cid}:{sid}:not-a-date:messages") is None
