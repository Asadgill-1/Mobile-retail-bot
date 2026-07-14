"""AUDIT — shop-owner bot tenant isolation.

Two clients from the seed: Client A (+971500000001, Shop 01 + Shop 02) and Client B
(+971500000002, Shop 03). Each links a different Telegram id. Client B's owner must never see —
or even trigger a data query for — Client A's shops, and vice versa. Spies on the service calls
prove refusal happens BEFORE any data access, not after.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.db.in_memory import InMemoryTenantRepo
from app.telegram_bot import bot
from app.tenants.service import TenantService

OWNER_A = 111
OWNER_B = 222


class _Query:
    def __init__(self, data):
        self.data = data
        self.edits, self.markups = [], []

    async def answer(self):
        pass

    async def edit_message_text(self, text, reply_markup=None):
        self.edits.append(text)
        self.markups.append(reply_markup)


class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **_kw):
        self.sent.append(text)


def _ctx(svc):
    return SimpleNamespace(bot=_Bot(), chat_data={},
                           application=SimpleNamespace(bot_data={"tenant_service": svc}))


def _cb(data, user_id):
    return SimpleNamespace(callback_query=_Query(data),
                           effective_user=SimpleNamespace(id=user_id),
                           effective_chat=SimpleNamespace(id=1), message=None)


@pytest.fixture
async def world(monkeypatch):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr("app.audit.service.record", _noop)
    repo = InMemoryTenantRepo()
    repo.seed_default()
    assert await repo.link_client_telegram("+971500000001", OWNER_A)  # Client A
    assert await repo.link_client_telegram("+971500000002", OWNER_B)  # Client B
    svc = TenantService(repo)
    shops = await repo.list_shops()
    shop_a = next(s for s in shops if s.name.startswith("Shop 01"))
    shop_b = next(s for s in shops if s.name.startswith("Shop 03"))
    return SimpleNamespace(svc=svc, shop_a=shop_a, shop_b=shop_b)


@pytest.mark.asyncio
async def test_shop_picker_shows_only_own_shops(world):
    ctx = _ctx(world.svc)
    upd = _cb("sshops", OWNER_B)
    await bot._shopowner_cb(upd, ctx)
    labels = [b.text for row in upd.callback_query.markups[-1].inline_keyboard for b in row]
    assert any("Shop 03" in x for x in labels)
    assert not any("Shop 01" in x or "Shop 02" in x for x in labels)


@pytest.mark.asyncio
async def test_foreign_profit_refused_before_data_access(world, monkeypatch):
    async def _profit(*a, **k):
        raise AssertionError("cross-tenant profit query must never run")

    monkeypatch.setattr(bot, "profit_summary", _profit)
    ctx = _ctx(world.svc)
    await bot._shopowner_cb(_cb(f"sprof:{world.shop_a.id}:today", OWNER_B), ctx)
    assert "not found" in ctx.bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_foreign_transcript_refused_before_data_access(world, monkeypatch):
    async def _transcript(*a, **k):
        raise AssertionError("cross-tenant transcript query must never run")

    monkeypatch.setattr(bot, "transcript", _transcript)
    ctx = _ctx(world.svc)
    await bot._shopowner_cb(_cb(f"smsgc:{world.shop_a.id}:+971501234567", OWNER_B), ctx)
    assert "not found" in ctx.bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_own_shop_succeeds(world, monkeypatch):
    from app.orders.models import ProfitSummary
    calls = {}

    async def _profit(shop_id, start, end, client=None):
        calls["shop"] = shop_id
        return ProfitSummary()

    monkeypatch.setattr(bot, "profit_summary", _profit)
    ctx = _ctx(world.svc)
    await bot._shopowner_cb(_cb(f"sprof:{world.shop_a.id}:today", OWNER_A), ctx)
    assert calls["shop"] == world.shop_a.id
    assert "Profit Report" in ctx.bot.sent[-1]


@pytest.mark.asyncio
async def test_stranger_gets_nothing(world, monkeypatch):
    async def _profit(*a, **k):
        raise AssertionError("unlinked user must trigger no query")

    monkeypatch.setattr(bot, "profit_summary", _profit)
    ctx = _ctx(world.svc)
    await bot._shopowner_cb(_cb(f"sprof:{world.shop_a.id}:today", 999999), ctx)
    assert "not linked" in ctx.bot.sent[-1].lower()
