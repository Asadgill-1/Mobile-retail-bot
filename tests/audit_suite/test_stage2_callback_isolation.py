"""AUDIT STAGE 2 — Inline-keyboard callback isolation.

A user taps a button; Telegram sends a `callback_query` whose `data` we control-parse. The tenant
binding must come from the AUTHENTICATED context (the keeper bot's `bot_data['shop']`, the rider's
Telegram id), never from the button payload — which carries only an order/request NUMBER. So a
spoofed or replayed payload cannot act on another shop's data:

- A keeper of shop A tapping a payload for shop B's order → shop-scoped lookup misses → fail closed.
- A forged `shop_id` appended to the callback data is ignored; the query stays bound to shop A.
- A rider tapping a payload for someone else's delivery → NotYourDelivery, fail closed.
- A non-staff stranger's callback to a keeper bot → blocked by the auth gate before any handler.

Pass: every cross-tenant / spoofed button action fails closed with an explicit exception surfaced
as a safe "not found" reply, and no state-changing service call runs on the wrong tenant.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from telegram import Chat, Message, Update, User

from app.telegram_bot import bot
from app.orders.service import NoPendingDraft
from app.riders.service import NotYourDelivery

SHOP_A, SHOP_B = uuid4(), uuid4()


class _Query:
    def __init__(self, data): self.data, self.edits = data, []
    async def answer(self): pass
    async def edit_message_text(self, text, reply_markup=None): self.edits.append(text)


class _Bot:
    def __init__(self): self.sent = []
    async def send_message(self, chat_id, text, **_kw): self.sent.append(text)


class _Ctx:
    def __init__(self, shop=None):
        self.bot = _Bot()
        self.chat_data = {}
        self.application = SimpleNamespace(bot_data={"shop": shop, "tenant_service": None})


def _cb(data, user_id=1):
    return SimpleNamespace(callback_query=_Query(data), effective_user=User(id=user_id, first_name="T",
                           is_bot=False), effective_chat=SimpleNamespace(id=1), message=None)


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    async def _noop(*a, **k): return None
    monkeypatch.setattr("app.audit.service.record", _noop)


# --- keeper: a button payload for another shop's order fails closed ----------
@pytest.fixture
def confirm_spy(monkeypatch):
    """confirm_order that enforces order→shop ownership (mirrors the real shop-scoped _get_draft)."""
    owner = {11: SHOP_A, 77: SHOP_B}  # order 77 belongs to shop B
    seen = []

    async def _confirm(shop, num):
        seen.append((shop.id, num))
        if owner.get(num) != shop.id:
            raise NoPendingDraft(num)   # shop-scoped miss — exactly what the real DB lookup does
        return {"order_number": num}

    monkeypatch.setattr(bot, "confirm_order", _confirm)
    return SimpleNamespace(seen=seen)


@pytest.mark.asyncio
async def test_keeper_cannot_confirm_another_shops_order(confirm_spy):
    ctx = _Ctx(shop=SimpleNamespace(id=SHOP_A, name="Shop A"))
    await bot._keeper_cb(_cb("kconf:77"), ctx)   # 77 is shop B's order
    # Bound to the authenticated shop A, not shop B; and it failed closed.
    assert confirm_spy.seen == [(SHOP_A, 77)]
    assert "No pending order draft #77" in ctx.bot.sent[-1]
    assert not any("confirmed" in m.lower() for m in ctx.bot.sent)


@pytest.mark.asyncio
async def test_forged_shop_id_in_callback_data_is_ignored(confirm_spy):
    """Appending shop B's id to the payload must not redirect the action away from shop A."""
    ctx = _Ctx(shop=SimpleNamespace(id=SHOP_A, name="Shop A"))
    await bot._keeper_cb(_cb(f"kconf:11:{SHOP_B}"), ctx)  # forged trailing shop id
    assert confirm_spy.seen == [(SHOP_A, 11)]             # still bound to shop A, order 11
    assert any("confirmed" in m.lower() for m in ctx.bot.sent)  # its own order confirmed fine


@pytest.mark.asyncio
async def test_keeper_own_order_confirms(confirm_spy):
    ctx = _Ctx(shop=SimpleNamespace(id=SHOP_A, name="Shop A"))
    await bot._keeper_cb(_cb("kconf:11"), ctx)
    assert confirm_spy.seen == [(SHOP_A, 11)]
    assert any("confirmed" in m.lower() for m in ctx.bot.sent)


# --- rider: a button payload for another rider's delivery fails closed -------
@pytest.mark.asyncio
async def test_rider_cannot_accept_foreign_delivery(monkeypatch):
    async def _by_tg(tid):
        return [{"id": "rider-self", "name": "Ali", "shop_id": str(SHOP_A)}]

    async def _set_custody(rider_ids, name, num, accept):
        # Real set_custody scopes by rider_ids via _get_my_order; foreign order → NotYourDelivery.
        if num not in (5,):            # 5 is this rider's only assignment
            raise NotYourDelivery(num)

    monkeypatch.setattr("app.riders.service.riders_by_telegram", _by_tg)
    monkeypatch.setattr("app.riders.service.set_custody", _set_custody)

    ctx = _Ctx()
    await bot._rider_cb(_cb("racc:999", user_id=555), ctx)   # 999 is not theirs
    assert any("No delivery #999" in m for m in ctx.bot.sent)  # fail closed, explicit


# --- stranger: not staff → blocked by the gate before any dispatcher ---------
@pytest.mark.asyncio
async def test_non_staff_callback_blocked_by_auth_gate(monkeypatch):
    from telegram.ext import ApplicationHandlerStop

    from app.db.in_memory import InMemoryTenantRepo

    repo = InMemoryTenantRepo(); repo.seed_default()
    shop = (await repo.list_shops())[0]
    monkeypatch.setattr("app.db.factory.get_tenant_repo", lambda: repo)

    ctx = _Ctx(shop=shop)
    stranger_cb = SimpleNamespace(
        callback_query=_Query("kconf:11"),
        effective_user=User(id=999999, first_name="Mallory", is_bot=False),
        effective_chat=SimpleNamespace(id=1), message=None,
    )
    with pytest.raises(ApplicationHandlerStop):     # gate stops the whole chain — dispatcher never runs
        await bot._keeper_auth_gate(stranger_cb, ctx)
