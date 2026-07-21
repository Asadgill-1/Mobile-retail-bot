"""Platform-owner console channel (migrations 024/025): health snapshot + redis_ops drain.

The console has no Redis access, so these two tables ARE the interface. What matters:
one bad op must not wedge the queue, and neither feature may break the health beat.
"""

from __future__ import annotations

from types import SimpleNamespace

import fakeredis.aioredis
import pytest

import app.console.service as console
from app.console.service import drain_ops, publish_snapshot


class _FakeSB:
    """Minimal supabase stub: records upserts/updates, serves a fixed redis_ops queue."""

    def __init__(self, pending: list[dict] | None = None):
        self.pending = pending or []
        self.upserts: list[dict] = []
        self.updates: list[tuple[str, dict]] = []
        self._t = ""
        self._patch: dict = {}
        self._id = ""

    def table(self, name):
        self._t = name
        return self

    def upsert(self, row):
        self.upserts.append(row)
        return self

    def select(self, *_a):
        return self

    def update(self, patch):
        self._patch = patch
        return self

    def eq(self, _col, val):
        self._id = val
        return self

    def is_(self, *_a):
        return self

    def order(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        if self._t == "redis_ops" and self._patch:
            self.updates.append((self._id, self._patch))
            self._patch = {}
            return SimpleNamespace(data=[])
        if self._t == "redis_ops":
            return SimpleNamespace(data=self.pending)
        return SimpleNamespace(data=[])


@pytest.fixture
def redis() -> fakeredis.aioredis.FakeRedis:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.mark.asyncio
async def test_snapshot_publishes_health_and_quarantined_list(redis):
    from app.security.service import quarantine_key

    await redis.set(quarantine_key("p1"), "1")
    sb = _FakeSB()
    report = SimpleNamespace(ok=True, checks={"db": "ok"}, metrics={"quarantined": 1})

    await publish_snapshot(report, redis, client=sb)

    row = sb.upserts[0]
    assert row["key"] == "health_snapshot"
    assert row["value"]["ok"] is True and row["value"]["checks"] == {"db": "ok"}
    assert row["value"]["quarantined"] == ["p1"]  # identity, not the raw Redis key
    assert row["value"]["at"]  # timestamp so the console can call a stale snapshot "offline"


@pytest.mark.asyncio
async def test_snapshot_never_raises_when_redis_scan_fails(redis):
    class _BadRedis:
        def scan_iter(self, **_kw):
            raise RuntimeError("redis down")

    sb = _FakeSB()
    report = SimpleNamespace(ok=False, checks={"redis": "down"}, metrics={})
    await publish_snapshot(report, _BadRedis(), client=sb)  # must not raise
    assert sb.upserts[0]["value"]["quarantined"] == []  # degraded, still published


@pytest.mark.asyncio
async def test_drain_executes_ops_through_the_real_security_functions(redis):
    from app.security.service import bypass_key, quarantine_key

    await redis.set(quarantine_key("p1"), "1")
    sb = _FakeSB(pending=[
        {"id": "op1", "op": "quarantine_lift", "args": {"identity": "p1"}},
        {"id": "op2", "op": "bypass_set", "args": {"identity": "p2"}},
    ])

    assert await drain_ops(redis, client=sb) == 2
    assert await redis.exists(quarantine_key("p1")) == 0  # lifted for real
    assert await redis.exists(bypass_key("p2")) == 1      # bypass really set
    assert all("applied_at" in patch for _, patch in sb.updates)


@pytest.mark.asyncio
async def test_one_bad_op_is_recorded_and_the_queue_keeps_moving(redis):
    from app.security.service import bypass_key

    sb = _FakeSB(pending=[
        {"id": "bad", "op": "quarantine_lift", "args": {}},          # missing identity
        {"id": "unknown", "op": "llm_test", "args": {}},             # will fail: no LLM in tests
        {"id": "good", "op": "bypass_set", "args": {"identity": "p9"}},
    ])

    processed = await drain_ops(redis, client=sb)

    assert processed == 3
    assert await redis.exists(bypass_key("p9")) == 1  # the good op still ran
    by_id = dict(sb.updates)
    assert "error" in by_id["bad"] and "identity" in by_id["bad"]["error"]
    assert "applied_at" not in by_id["bad"]  # a failure is never marked applied


@pytest.mark.asyncio
async def test_drain_survives_a_dead_settings_table(redis):
    class _DeadSB:
        def table(self, _n):
            raise RuntimeError("supabase down")

    assert await drain_ops(redis, client=_DeadSB()) == 0  # returns, never raises
