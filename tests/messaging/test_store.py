"""Message archive (migration 009): store CRUD against a recording fake supabase client, plus
the remember() hook — one persist per turn, and a DB failure never breaks the chat flow."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import fakeredis.aioredis
import pytest

from app.escalations.context import history, remember
from app.messaging.store import conversations, delete_messages, save_message, transcript

SID = uuid4()


class _Fake:
    """Chainable supabase stand-in that records the query it was asked to build."""

    def __init__(self, rows=None, fail=False):
        self.rows = rows or []
        self.fail = fail
        self.inserts: list[dict] = []
        self.filters: list[tuple] = []
        self.deleting = False

    def table(self, name):
        self.table_name = name
        return self

    def insert(self, payload):
        self.inserts.append(payload)
        return self

    def select(self, cols):
        return self

    def delete(self):
        self.deleting = True
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def gte(self, col, val):
        self.filters.append(("gte", col, val))
        return self

    def lt(self, col, val):
        self.filters.append(("lt", col, val))
        return self

    def order(self, col, desc=False):
        return self

    def limit(self, n):
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("db down")
        return SimpleNamespace(data=self.rows)


@pytest.mark.asyncio
async def test_save_message_inserts_row():
    sb = _Fake()
    await save_message(SID, "+971501234567", "customer", "hi", client=sb)
    assert sb.inserts == [{"shop_id": str(SID), "identity": "+971501234567",
                           "role": "customer", "content": "hi"}]


@pytest.mark.asyncio
async def test_save_message_never_raises():
    await save_message(SID, "x", "customer", "hi", client=_Fake(fail=True))  # must not propagate


@pytest.mark.asyncio
async def test_transcript_is_oldest_first():
    rows_desc = [{"role": "assistant", "content": "b", "created_at": "t2"},
                 {"role": "customer", "content": "a", "created_at": "t1"}]
    out = await transcript(SID, "+971", client=_Fake(rows=rows_desc))
    assert [r["content"] for r in out] == ["a", "b"]


@pytest.mark.asyncio
async def test_conversations_dedupes_newest_first():
    rows = [{"identity": "A", "created_at": "t3"}, {"identity": "B", "created_at": "t2"},
            {"identity": "A", "created_at": "t1"}]
    out = await conversations(SID, client=_Fake(rows=rows))
    assert [c["identity"] for c in out] == ["A", "B"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs,expect", [
    ({"shop_id": SID}, [("eq", "shop_id", str(SID))]),
    ({"start": datetime(2026, 1, 1, tzinfo=UTC), "end": datetime(2026, 2, 1, tzinfo=UTC)},
     [("gte", "created_at", "2026-01-01T00:00:00+00:00"),
      ("lt", "created_at", "2026-02-01T00:00:00+00:00")]),
    ({}, [("gte", "created_at", "1970-01-01T00:00:00+00:00")]),  # ALL still carries a WHERE
])
async def test_delete_messages_filters(kwargs, expect):
    sb = _Fake(rows=[{}, {}])
    n = await delete_messages(client=sb, **kwargs)
    assert sb.deleting and sb.filters == expect
    assert len(sb.filters) >= 1  # a bare DELETE must be impossible
    assert n == 2


# --- the remember() hook ----------------------------------------------------
@pytest.mark.asyncio
async def test_remember_persists_each_turn_once(monkeypatch):
    calls = []

    async def _spy(shop_id, identity, role, content, client=None):
        calls.append((shop_id, identity, role, content))

    monkeypatch.setattr("app.messaging.store.save_message", _spy)
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await remember(r, SID, "+971", "customer", "hi")
    assert calls == [(SID, "+971", "customer", "hi")]
    assert await history(r, SID, "+971") == [{"role": "customer", "content": "hi"}]


@pytest.mark.asyncio
async def test_remember_survives_persist_failure(monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.messaging.store.save_message", _boom)
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await remember(r, SID, "+971", "customer", "hi")  # must not raise
    # redis unharmed despite the DB failure
    assert await history(r, SID, "+971") == [{"role": "customer", "content": "hi"}]
