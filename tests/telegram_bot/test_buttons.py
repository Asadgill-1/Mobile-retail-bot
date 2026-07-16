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
        self.markups: list = []

    async def answer(self) -> None:
        self.answered = True

    async def edit_message_text(self, text, reply_markup=None) -> None:
        self.edits.append(text)
        self.markups.append(reply_markup)


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


# --- shop-owner keyboards ---------------------------------------------------
def test_shopowner_menu_and_shop_actions_cover_everything():
    menu = {kb.parse_cb(b.callback_data)[0]
            for row in kb.shopowner_menu().inline_keyboard for b in row}
    assert {"sshops", "sanmenu", "smsgs"} <= menu
    actions = {kb.parse_cb(b.callback_data)[0]
               for row in kb.shopowner_shop_actions("u" * 36).inline_keyboard for b in row}
    assert {"sprofmenu", "sordmenu", "sinv", "scod", "sexpmenu", "smsg"} <= actions
    ana = {kb.parse_cb(b.callback_data)[0]
           for row in kb.shopowner_analytics_menu().inline_keyboard for b in row}
    assert {"scmpmenu", "stopmenu", "scanmenu", "scodall"} <= ana


def test_shopowner_conversations_kb_worst_case_fits():
    sid = "11111111-1111-1111-1111-111111111111"
    m = kb.shopowner_conversations_kb(sid, [{"identity": "+971501234567"}])
    data = m.inline_keyboard[0][0].callback_data
    assert len(data.encode()) <= kb.CB_LIMIT
    assert kb.parse_cb(data) == ("smsgc", [sid, "+971501234567"])


# --- shop-owner dispatch ----------------------------------------------------
from uuid import UUID, uuid4  # noqa: E402


class _SoSvc:
    """Fake TenantService for the shop-owner dispatcher: one linked client + their shops."""

    def __init__(self, client_id=None, shops=None, linked=True):
        self.client_id = client_id or uuid4()
        self.client = SimpleNamespace(id=self.client_id, name="TechStore Group")
        self.shops = shops or []
        self.linked = linked

    async def client_by_telegram(self, tid):
        return self.client if self.linked else None

    async def get_shop(self, sid: UUID):
        for s in self.shops:
            if s.id == sid:
                return s
        from app.tenants.service import ShopNotFound
        raise ShopNotFound(str(sid))

    async def list_shops_by_client(self, cid):
        return [s for s in self.shops if s.client_id == cid]


def _own(svc, name="Shop 01"):
    s = SimpleNamespace(id=uuid4(), client_id=svc.client_id, name=name)
    svc.shops.append(s)
    return s


def _foreign(svc, name="Rival"):
    s = SimpleNamespace(id=uuid4(), client_id=uuid4(), name=name)
    svc.shops.append(s)
    return s


@pytest.mark.asyncio
async def test_shopowner_profit_button_routes_to_profit_summary(monkeypatch):
    from app.orders.models import ProfitSummary
    calls = {}

    async def _profit(shop_id, start, end, client=None):
        calls["profit"] = shop_id
        return ProfitSummary()

    monkeypatch.setattr(bot, "profit_summary", _profit)
    svc = _SoSvc()
    shop = _own(svc)
    ctx = _Ctx(service=svc)
    await bot._shopowner_cb(_cb_update(f"sprof:{shop.id}:today", user_id=999), ctx)
    assert calls["profit"] == shop.id
    assert "Profit Report" in ctx.bot.sent[-1]


@pytest.mark.asyncio
async def test_shopowner_unlinked_user_denied(monkeypatch):
    async def _profit(*a, **k):
        raise AssertionError("must not be called")

    monkeypatch.setattr(bot, "profit_summary", _profit)
    svc = _SoSvc(linked=False)
    shop = _own(svc)
    ctx = _Ctx(service=svc)
    await bot._shopowner_cb(_cb_update(f"sprof:{shop.id}:today", user_id=999), ctx)
    assert "not linked" in ctx.bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_shopowner_foreign_shop_fails_closed(monkeypatch):
    async def _profit(*a, **k):
        raise AssertionError("cross-tenant data call must never happen")

    monkeypatch.setattr(bot, "profit_summary", _profit)
    svc = _SoSvc()
    rival = _foreign(svc)  # exists, but belongs to another client
    ctx = _Ctx(service=svc)
    await bot._shopowner_cb(_cb_update(f"sprof:{rival.id}:today", user_id=999), ctx)
    assert "not found" in ctx.bot.sent[-1].lower()


@pytest.mark.asyncio
async def test_shopowner_shop_picker_lists_only_own_shops():
    svc = _SoSvc()
    _own(svc, "Shop 01")
    _own(svc, "Shop 02")
    _foreign(svc, "Rival Shop")
    ctx = _Ctx(service=svc)
    upd = _cb_update("sshops", user_id=999)
    await bot._shopowner_cb(upd, ctx)
    q = upd.callback_query
    labels = [b.text for row in q.markups[-1].inline_keyboard for b in row]
    assert any("Shop 01" in x for x in labels) and any("Shop 02" in x for x in labels)
    assert not any("Rival" in x for x in labels)


# --- owner delete-messages flow ----------------------------------------------
@pytest.mark.asyncio
async def test_owner_delete_range_flow(monkeypatch):
    calls = {}

    async def _del(shop_id=None, start=None, end=None):
        calls["del"] = (shop_id, start, end)
        return 4

    monkeypatch.setattr(bot, "delete_messages", _del)
    ctx = _Ctx(service=None)
    await bot._owner_cb(_cb_update("omdel:range"), ctx)
    assert ctx.chat_data["pending"] == {"do": "omdel", "args": ["range"]}
    await bot._owner_text(_text_update("2026-01-01 2026-01-31"), ctx)
    shop_id, start, end = calls["del"]
    assert shop_id is None and start is not None and end is not None
    assert start.date().isoformat() == "2026-01-01"
    assert "4 message(s) deleted" in ctx.bot.sent[-1]


@pytest.mark.asyncio
async def test_owner_delete_all_requires_yes(monkeypatch):
    calls = {"n": 0}

    async def _del(**kw):
        calls["n"] += 1
        return 0

    monkeypatch.setattr(bot, "delete_messages", _del)
    ctx = _Ctx(service=None)
    await bot._owner_cb(_cb_update("omdel:all"), ctx)
    await bot._owner_text(_text_update("no"), ctx)
    assert calls["n"] == 0 and "cancelled" in ctx.bot.sent[-1].lower()
    await bot._owner_cb(_cb_update("omdel:all"), ctx)
    await bot._owner_text(_text_update("YES"), ctx)
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_owner_delete_by_shop(monkeypatch):
    calls = {}

    async def _del(shop_id=None, start=None, end=None):
        calls["del"] = shop_id
        return 2

    monkeypatch.setattr(bot, "delete_messages", _del)
    sid = "11111111-1111-1111-1111-111111111111"
    ctx = _Ctx(service=None)
    await bot._owner_cb(_cb_update(f"omdel:shop:{sid}"), ctx)
    await bot._owner_text(_text_update("YES"), ctx)
    assert str(calls["del"]) == sid


# --- owner: analytics, onboarding, escalation resolve (Phase 4) ---
def test_owner_menu_has_analytics_and_onboarding():
    actions = {kb.parse_cb(b.callback_data)[0]
               for row in kb.owner_menu().inline_keyboard for b in row}
    assert {"otopmenu", "ocanmenu", "ocodall", "oonb"} <= actions


def test_owner_onboarding_menu_actions():
    actions = {kb.parse_cb(b.callback_data)[0]
               for row in kb.owner_onboarding_menu().inline_keyboard for b in row}
    assert {"oaddc", "oadds", "oaddtmenu", "oaddkmenu"} <= actions


def test_owner_escalation_actions_carries_shop_and_identity():
    m = kb.owner_escalation_actions("s-uuid", "971501234567")
    assert kb.parse_cb(m.inline_keyboard[0][0].callback_data) == ("oesr", ["s-uuid", "971501234567"])


def test_owner_escalation_actions_none_when_payload_too_long():
    # A pathological identity must not crash the whole escalation list.
    assert kb.owner_escalation_actions("s" * 36, "i" * 40) is None


@pytest.mark.asyncio
async def test_owner_resolve_button_calls_resolve_escalation(monkeypatch):
    from uuid import uuid4

    calls = {}
    sid = uuid4()

    async def _resolve(redis, shop_id, identity, client=None):
        calls["args"] = (shop_id, identity)
        return 1

    monkeypatch.setattr("app.escalations.service.resolve_escalation", _resolve)
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: object())

    ctx = _Ctx(service=None)
    await bot._owner_cb(_cb_update(kb.cb("oesr", str(sid), "971501234567")), ctx)

    assert calls["args"] == (sid, "971501234567")
    assert any("Resolved" in s for s in ctx.bot.sent)


@pytest.mark.asyncio
async def test_owner_resolve_button_reports_already_resolved(monkeypatch):
    from uuid import uuid4

    async def _resolve(redis, shop_id, identity, client=None):
        return 0  # nothing was open

    monkeypatch.setattr("app.escalations.service.resolve_escalation", _resolve)
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: object())

    ctx = _Ctx(service=None)
    await bot._owner_cb(_cb_update(kb.cb("oesr", str(uuid4()), "p1")), ctx)
    assert any("already resolved" in s for s in ctx.bot.sent)


@pytest.mark.asyncio
async def test_owner_top_products_button_uses_all_shops(monkeypatch):
    from uuid import uuid4

    from app.orders.models import ProfitSummary

    seen = {}
    shops = [SimpleNamespace(id=uuid4(), name="Shop 01"), SimpleNamespace(id=uuid4(), name="Shop 02")]

    class _Svc:
        async def list_shops(self):
            return shops

    async def _profit(shop_id, start, end):
        seen.setdefault("shops", []).append(shop_id)
        return ProfitSummary()

    def _fmt(items, label):
        seen["items"] = items
        seen["label"] = label
        return "🏆 top"

    monkeypatch.setattr(bot, "profit_summary", _profit)
    monkeypatch.setattr("app.reports.service.format_top_products", _fmt)

    ctx = _Ctx(service=_Svc())
    await bot._owner_cb(_cb_update(kb.cb("otop", "weekly")), ctx)

    assert seen["shops"] == [s.id for s in shops]          # every shop, not one client's
    assert [n for n, _ in seen["items"]] == ["Shop 01", "Shop 02"]
    assert "🏆 top" in ctx.bot.sent


@pytest.mark.asyncio
async def test_owner_add_client_button_prompts_then_text_creates(monkeypatch):
    from uuid import uuid4

    created = {}

    class _Svc:
        async def create_client(self, name, contact=None, phone=None, email=None):
            created["args"] = (name, contact, phone, email)
            return SimpleNamespace(id=uuid4(), name=name)

    ctx = _Ctx(service=_Svc())
    await bot._owner_cb(_cb_update(kb.cb("oaddc")), ctx)
    assert ctx.chat_data["pending"] == {"do": "oaddc", "args": []}

    await bot._owner_text(_text_update("Gulf Traders; Asad; 0501234567; a@b.ae"), ctx)
    assert created["args"] == ("Gulf Traders", "Asad", "0501234567", "a@b.ae")
    assert any("Client added" in s for s in ctx.bot.sent)


@pytest.mark.asyncio
async def test_owner_set_tokens_dash_keeps_existing(monkeypatch):
    from uuid import uuid4

    seen = {}
    sid = uuid4()

    class _Svc:
        async def set_shop_tokens(self, shop_id, keeper, customer):
            seen["args"] = (shop_id, keeper, customer)
            return SimpleNamespace(name="Shop 01")

    ctx = _Ctx(service=_Svc())
    ctx.chat_data["pending"] = {"do": "oaddt", "args": [str(sid)]}
    await bot._owner_text(_text_update("new-keeper; -"), ctx)

    assert seen["args"] == (sid, "new-keeper", None)  # `-` → None → untouched


def test_fields_splits_on_semicolons_and_pads():
    assert bot._fields("a; b; c", 4) == ["a", "b", "c", ""]
    assert bot._fields("Gulf Mobiles", 2) == ["Gulf Mobiles", ""]  # names may contain spaces
    assert bot._fields("a;b;c;d;e", 3) == ["a", "b", "c"]
