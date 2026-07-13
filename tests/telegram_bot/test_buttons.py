"""Inline-button layer: keyboard builders (pure) + callback dispatch routing.

Dispatchers are called directly with a fake callback update; service calls are monkeypatched,
so no Telegram connection and no DB are needed. We assert the button routes to the SAME service
call the slash command uses, with the argument parsed out of callback_data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from telegram import Chat, Message, Update, User

from app.telegram_bot import bot
from app.telegram_bot import keyboards as kb

OWNER = 100000001  # matches OWNER_TELEGRAM_ID in conftest env


# --- pure keyboards --------------------------------------------------------
def test_cb_roundtrip_and_limit():
    assert kb.parse_cb(kb.cb("kasgr", 7, "u" * 36)) == ("kasgr", ["7", "u" * 36])
    assert kb.parse_cb("kmenu") == ("kmenu", [])


def test_cb_rejects_overlong():
    with pytest.raises(AssertionError):
        kb.cb("x" * 70)


@pytest.mark.parametrize("custody,status,expect", [
    ("none", "shipped", ["racc", "rnrx"]),      # fresh handover → accept / not-received
    ("offered", "shipped", ["racc", "rnrx"]),
    ("accepted", "shipped", ["rdel", "rcan"]),  # picked up → deliver / cancel
    ("disputed", "shipped", None),              # already disputed → no actions
    ("accepted", "delivered", None),            # done → no actions
])
def test_rider_delivery_actions_state_matrix(custody, status, expect):
    markup = kb.rider_delivery_actions(7, custody, status)
    if expect is None:
        assert markup is None
        return
    actions = [kb.parse_cb(b.callback_data)[0] for row in markup.inline_keyboard for b in row]
    assert actions == expect


def test_keeper_menu_has_expected_actions():
    actions = {kb.parse_cb(b.callback_data)[0]
               for row in kb.keeper_menu().inline_keyboard for b in row}
    assert {"korders", "kpr", "kriders", "kprofmenu", "kexpmenu", "knegmenu"} <= actions


def test_rider_picker_carries_order_and_rider():
    m = kb.keeper_rider_picker(7, [{"id": "r-uuid", "name": "Ali"}])
    assert kb.parse_cb(m.inline_keyboard[0][0].callback_data) == ("kasgr", ["7", "r-uuid"])


# --- dispatch fakes --------------------------------------------------------
class _Query:
    def __init__(self, data: str) -> None:
        self.data = data
        self.answered = False
        self.edits: list[str] = []

    async def answer(self) -> None:
        self.answered = True

    async def edit_message_text(self, text, reply_markup=None) -> None:
        self.edits.append(text)


class _Bot:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, chat_id, text, **_kw) -> None:
        self.sent.append(text)


class _Ctx:
    def __init__(self, service=None, shop=None) -> None:
        self.bot = _Bot()
        self.chat_data: dict = {}
        self.application = SimpleNamespace(bot_data={"tenant_service": service, "shop": shop})


def _cb_update(data: str, user_id: int = OWNER, chat_id: int = 1) -> Update:
    user = User(id=user_id, first_name="T", is_bot=False)
    return SimpleNamespace(callback_query=_Query(data), effective_user=user,
                           effective_chat=SimpleNamespace(id=chat_id), message=None)


def _text_update(text: str, user_id: int = OWNER, chat_id: int = 1) -> Update:
    user = User(id=user_id, first_name="T", is_bot=False)
    chat = Chat(id=chat_id, type="private")
    msg = Message(message_id=1, date=datetime.now(tz=timezone.utc), chat=chat, from_user=user, text=text)
    return Update(update_id=1, message=msg)


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("app.audit.service.record", _noop)


# --- rider dispatch --------------------------------------------------------
@pytest.mark.asyncio
async def test_rider_accept_button_calls_set_custody(monkeypatch):
    calls = {}

    async def _by_tg(tid):
        return [{"id": "r1", "name": "Ali", "shop_id": "s1"}]

    async def _set_custody(ids, name, num, accept):
        calls["set"] = (ids, name, num, accept)

    monkeypatch.setattr("app.riders.service.riders_by_telegram", _by_tg)
    monkeypatch.setattr("app.riders.service.set_custody", _set_custody)
    ctx = _Ctx()
    await bot._rider_cb(_cb_update("racc:7", user_id=555), ctx)
    assert calls["set"] == (["r1"], "Ali", 7, True)


@pytest.mark.asyncio
async def test_rider_cancel_button_sets_pending_remarks(monkeypatch):
    async def _by_tg(tid):
        return [{"id": "r1", "name": "Ali", "shop_id": "s1"}]
    monkeypatch.setattr("app.riders.service.riders_by_telegram", _by_tg)
    ctx = _Ctx()
    await bot._rider_cb(_cb_update("rcan:9", user_id=555), ctx)
    assert ctx.chat_data["await_remarks"] == 9
    assert "reason" in ctx.bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_rider_cancel_remarks_reply_calls_cancel(monkeypatch):
    calls = {}

    async def _by_tg(tid):
        return [{"id": "r1", "name": "Ali", "shop_id": "s1"}]

    async def _cancel(ids, name, num, remarks):
        calls["cancel"] = (ids, name, num, remarks)

    monkeypatch.setattr("app.riders.service.riders_by_telegram", _by_tg)
    monkeypatch.setattr("app.riders.service.cancel_delivery", _cancel)
    ctx = _Ctx()
    ctx.chat_data["await_remarks"] = 9
    await bot._rider_cash(_text_update("customer not home", user_id=555), ctx)
    assert calls["cancel"] == (["r1"], "Ali", 9, "customer not home")
    assert "await_remarks" not in ctx.chat_data


# --- keeper dispatch -------------------------------------------------------
@pytest.mark.asyncio
async def test_keeper_confirm_button_calls_confirm_order(monkeypatch):
    calls = {}

    async def _confirm(shop, num):
        calls["confirm"] = (shop, num)

    monkeypatch.setattr(bot, "confirm_order", _confirm)
    shop = SimpleNamespace(id="s1", name="TechWorld")
    ctx = _Ctx(shop=shop)
    await bot._keeper_cb(_cb_update("kconf:12"), ctx)
    assert calls["confirm"] == (shop, 12)


@pytest.mark.asyncio
async def test_keeper_deliveryupdate_button_calls_advance(monkeypatch):
    calls = {}

    async def _adv(shop, num, step):
        calls["adv"] = (num, step)

    monkeypatch.setattr(bot, "advance_delivery", _adv)
    ctx = _Ctx(shop=SimpleNamespace(id="s1", name="X"))
    await bot._keeper_cb(_cb_update("kdup:5:packed"), ctx)
    assert calls["adv"] == (5, "packed")


@pytest.mark.asyncio
async def test_keeper_negotiation_button_calls_set(monkeypatch):
    calls = {}

    async def _neg(shop_id, on):
        calls["neg"] = (shop_id, on)

    monkeypatch.setattr(bot, "set_negotiation", _neg)
    ctx = _Ctx(shop=SimpleNamespace(id="s1", name="X"))
    await bot._keeper_cb(_cb_update("kneg:off"), ctx)
    assert calls["neg"] == ("s1", False)


@pytest.mark.asyncio
async def test_keeper_reject_button_then_reason_reply(monkeypatch):
    calls = {}

    async def _reject(shop, num, reason):
        calls["reject"] = (num, reason)

    monkeypatch.setattr(bot, "reject_order", _reject)
    shop = SimpleNamespace(id="s1", name="X")
    ctx = _Ctx(shop=shop)
    await bot._keeper_cb(_cb_update("krej:8"), ctx)
    assert ctx.chat_data["pending"] == {"do": "krej", "args": ["8"]}
    # keeper types the reason
    await bot._keeper_text(_text_update("out of stock"), ctx)
    assert calls["reject"] == (8, "out of stock")
    assert "pending" not in ctx.chat_data


# --- owner dispatch --------------------------------------------------------
@pytest.mark.asyncio
async def test_owner_resume_button_calls_service():
    calls = {}

    class _Svc:
        async def resume_shop(self, sid):
            calls["resume"] = sid
            return SimpleNamespace(name="Shop", status=SimpleNamespace(value="active"))

    ctx = _Ctx(service=_Svc())
    await bot._owner_cb(_cb_update("oresume:11111111-1111-1111-1111-111111111111"), ctx)
    assert str(calls["resume"]) == "11111111-1111-1111-1111-111111111111"


@pytest.mark.asyncio
async def test_owner_pause_button_then_reason_reply():
    calls = {}

    class _Svc:
        async def suspend_shop(self, sid, reason):
            calls["suspend"] = (str(sid), reason)
            return SimpleNamespace(name="Shop", suspension_reason=reason)

    ctx = _Ctx(service=_Svc())
    sid = "11111111-1111-1111-1111-111111111111"
    await bot._owner_cb(_cb_update(f"opause:{sid}"), ctx)
    assert ctx.chat_data["pending"] == {"do": "opause", "args": [sid]}
    await bot._owner_text(_text_update("fraud investigation"), ctx)
    assert calls["suspend"] == (sid, "fraud investigation")


@pytest.mark.asyncio
async def test_owner_button_denied_for_non_owner():
    ctx = _Ctx(service=None)
    await bot._owner_cb(_cb_update("odash", user_id=42), ctx)
    assert "owner only" in ctx.bot.sent[-1].lower()
