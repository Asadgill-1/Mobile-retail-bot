"""AUDIT STAGE 3 — Multi-tenant checkout concurrency, driven through Telegram.

Two identical Telegram checkout taps (the keeper's ✅ Confirm inline button → callback `kconf:N`)
fire at the same instant on a product with stock == 1. The confirmation routes through the real
`orders.service.confirm_order`, whose stock decrement is the atomic Supabase RPC (`decrement_stock`,
migration 003 — conditional `UPDATE … WHERE quantity >= n`, the row-lock guarantee).

The fake client below emulates that RPC's atomic conditional decrement under a real threading.Lock,
and `confirm_order` runs it inside `asyncio.to_thread`, so the two taps contend on real OS threads.

Pass: exactly one tap replies "confirmed"; the other replies with the out-of-stock string back to
Telegram; final stock == 0 (never negative, never split).
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.orders.service as svc
from app.telegram_bot import bot

PID = "prod-1"


class AtomicStockClient:
    """Emulates the decrement_stock RPC: atomic check-and-decrement (the SELECT FOR UPDATE guard)."""
    def __init__(self, stock: int) -> None:
        self.stock = {PID: stock}
        self._lock = threading.Lock()

    def rpc(self, name, params):
        client = self

        class _E:
            def execute(self):
                if name != "decrement_stock":
                    return SimpleNamespace(data=[])
                with client._lock:
                    have = client.stock.get(params["p_id"], 0)
                    if have >= params["n"]:
                        client.stock[params["p_id"]] = have - params["n"]
                        return SimpleNamespace(data=[{"quantity": client.stock[params["p_id"]]}])
                    return SimpleNamespace(data=[])
        return _E()


class _Query:
    def __init__(self, data): self.data = data
    async def answer(self): pass
    async def edit_message_text(self, text, reply_markup=None): pass


class _Bot:
    def __init__(self): self.sent = []
    async def send_message(self, chat_id, text, **_kw): self.sent.append(text)


class _Ctx:
    def __init__(self, shop):
        self.bot = _Bot()
        self.chat_data = {}
        self.application = SimpleNamespace(bot_data={"shop": shop, "tenant_service": None})


def _cb(data):
    return SimpleNamespace(callback_query=_Query(data), effective_user=SimpleNamespace(id=1),
                           effective_chat=SimpleNamespace(id=1), message=None)


@pytest.mark.asyncio
async def test_two_telegram_taps_one_confirms_one_out_of_stock(monkeypatch):
    client = AtomicStockClient(stock=1)
    draft = {"id": "o1", "product_id": PID, "quantity": 1, "status": "draft",
             "selling_price": "999", "discount_amount": "0", "address": "1 St", "phone": "cust",
             "delivery_date": None, "products": {"brand": "Redmi", "model": "13"}}

    async def _get_draft(shop_id, num, c): return dict(draft)
    async def _noop(*a, **k): return None

    monkeypatch.setattr(svc, "_get_draft", _get_draft)
    monkeypatch.setattr(svc, "_set_status", _noop)
    monkeypatch.setattr(svc, "send_to_customer", _noop)
    monkeypatch.setattr(svc, "_remember_to_customer", _noop)
    monkeypatch.setattr(svc, "record", _noop, raising=False)
    monkeypatch.setattr("app.audit.service.record", _noop)
    # Route the button's confirm_order through the real service, but with our atomic race client.
    monkeypatch.setattr(bot, "confirm_order",
                        lambda shop, num: svc.confirm_order(shop, num, client))

    shop = SimpleNamespace(id=uuid4(), name="Shop A")
    ctx1, ctx2 = _Ctx(shop), _Ctx(shop)
    await asyncio.gather(bot._keeper_cb(_cb("kconf:5"), ctx1),
                         bot._keeper_cb(_cb("kconf:5"), ctx2))

    replies = ctx1.bot.sent + ctx2.bot.sent
    # Success reply says "Stock updated"; the failure reply says "out of stock" (note it also
    # contains the word "confirmed" in "can't be confirmed" — so match on the distinct phrases).
    confirmed = [m for m in replies if "stock updated" in m.lower()]
    out = [m for m in replies if "out of stock" in m.lower()]
    assert len(confirmed) == 1, replies      # exactly one checkout succeeded
    assert len(out) == 1, replies            # the other got the out-of-stock string, in Telegram
    assert client.stock[PID] == 0            # never oversold, never negative
