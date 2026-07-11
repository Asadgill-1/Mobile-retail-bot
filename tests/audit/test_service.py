"""Audit trail (§16). Fake supabase client — no network. record is best-effort; recent reads back."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.audit.service import recent, record


class _FakeSB:
    def __init__(self, rows=None, boom=False):
        self.inserts: list[dict] = []
        self._rows = rows or []
        self._boom = boom
        self._op = ""

    def table(self, name):
        self._t = name
        return self

    def insert(self, row):
        self._op = "insert"
        self.inserts.append(row)
        return self

    def select(self, *a, **k):
        self._op = "select"
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def eq(self, *a):
        return self

    def execute(self):
        if self._boom:
            raise RuntimeError("db down")

        class _R:
            data = self._rows if self._op == "select" else [{"id": "audit-1"}]

        return _R()


@pytest.mark.asyncio
async def test_record_writes_row_with_actor_action_detail():
    sb = _FakeSB()
    shop = uuid4()
    await record("55", "confirmorder_cmd", shop_id=shop, detail={"text": "/confirmorder 7"}, client=sb)
    assert len(sb.inserts) == 1
    row = sb.inserts[0]
    assert row["actor"] == "55" and row["action"] == "confirmorder_cmd"
    assert row["shop_id"] == str(shop) and row["detail"] == {"text": "/confirmorder 7"}


@pytest.mark.asyncio
async def test_record_defaults_shop_none_and_empty_detail():
    sb = _FakeSB()
    await record("owner", "pauseshop", client=sb)
    row = sb.inserts[0]
    assert row["shop_id"] is None and row["detail"] == {}


@pytest.mark.asyncio
async def test_record_swallows_db_failure():
    # a failed audit must NEVER break the action it records
    await record("55", "blacklist_cmd", client=_FakeSB(boom=True))  # must not raise


@pytest.mark.asyncio
async def test_recent_returns_rows():
    rows = [{"actor": "55", "action": "confirmorder_cmd", "created_at": "2026-07-10T09:00:00Z"}]
    out = await recent(client=_FakeSB(rows=rows))
    assert out == rows
