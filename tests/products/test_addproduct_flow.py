"""`/addproduct` 12-step flow: the min-qty step + the ➕ button entry point.

Handlers are called directly with fake Update objects — no Telegram, no DB.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from telegram.ext import CallbackQueryHandler, CommandHandler

from app.products import addproduct_flow as flow


class _Msg:
    def __init__(self, text=""):
        self.text = text
        self.replies: list[str] = []
        self.photo = None
        self.video = None

    async def reply_text(self, text, **_kw):
        self.replies.append(text)


class _Query:
    def __init__(self, message):
        self.message = message
        self.answered = False

    async def answer(self):
        self.answered = True


class _Update:
    def __init__(self, text="", callback=False):
        self.message = _Msg(text)
        self.effective_message = self.message
        self.callback_query = _Query(self.message) if callback else None


class _Ctx:
    def __init__(self):
        self.user_data: dict = {}


@pytest.mark.asyncio
async def test_quantity_step_now_asks_min_qty_not_media():
    ctx = _Ctx()
    u = _Update("5")
    state = await flow.quantity(u, ctx)
    assert state == flow.MINQTY
    assert "Alert me when stock drops to" in u.message.replies[0]
    assert ctx.user_data[flow._DRAFT]["quantity"] == 5


@pytest.mark.asyncio
async def test_min_qty_step_stores_threshold_and_advances_to_media():
    ctx = _Ctx()
    u = _Update("2")
    state = await flow.min_qty(u, ctx)
    assert state == flow.MEDIA
    assert ctx.user_data[flow._DRAFT]["min_qty"] == 2
    assert "images" in u.message.replies[0]


@pytest.mark.asyncio
async def test_min_qty_rejects_junk_and_re_asks():
    ctx = _Ctx()
    u = _Update("soon")
    assert await flow.min_qty(u, ctx) is None  # None = stay in this state
    assert "⚠️" in u.message.replies[0]
    assert "min_qty" not in ctx.user_data.get(flow._DRAFT, {})


@pytest.mark.asyncio
async def test_button_entry_answers_callback_and_starts_flow():
    ctx = _Ctx()
    u = _Update(callback=True)
    state = await flow.start(u, ctx)
    assert state == flow.CATEGORY
    assert u.callback_query.answered is True  # otherwise Telegram spins the button forever
    assert "1/12 Category?" in u.message.replies[0]


@pytest.mark.asyncio
async def test_command_entry_still_works():
    ctx = _Ctx()
    u = _Update("/addproduct")
    assert await flow.start(u, ctx) == flow.CATEGORY
    assert "1/12 Category?" in u.message.replies[0]


def test_handler_has_both_entry_points():
    h = flow.build_addproduct_handler()
    kinds = [type(e) for e in h.entry_points]
    assert CommandHandler in kinds and CallbackQueryHandler in kinds
    assert flow.MINQTY in h.states


def test_summary_shows_the_alert_threshold():
    draft = {
        "category": "Mobile", "brand": "Samsung", "model": "S23", "color": "black",
        "condition": "New", "specs": {"ram": "12GB"},
        "cost_price": Decimal("1000"), "selling_price": Decimal("1500"),
        "quantity": 5, "min_qty": 2, "images": [], "video": None,
    }
    out = flow._summary(draft)
    assert "12/12 Confirm" in out and "Low-stock alert: 2" in out

    draft["min_qty"] = 0
    assert "Low-stock alert: off" in flow._summary(draft)  # 0 reads as off, not "0"
