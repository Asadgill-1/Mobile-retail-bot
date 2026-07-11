"""Tests for the Telegram bot — owner commands + auth gate + shopkeeper stubs.

Handlers are called directly with constructed PTB Update objects and a fake
bot/context, so no real Telegram connection is needed.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from telegram import Chat, Message, Update, User

from app.telegram_bot import bot

OWNER = 100000001  # matches OWNER_TELEGRAM_ID in conftest env


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    """Every privileged command writes an audit row (§16). Stub it — keep the suite offline."""
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr("app.audit.service.record", _noop)


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **_kw) -> None:
        self.sent.append((chat_id, text))


class FakeApp:
    def __init__(self, service) -> None:
        self.bot_data = {"tenant_service": service}


class FakeContext:
    def __init__(self, service) -> None:
        self.bot = FakeBot()
        self.application = FakeApp(service)


def _update(text: str, user_id: int, chat_id: int = 1) -> Update:
    user = User(id=user_id, first_name="Tester", is_bot=False)
    chat = Chat(id=chat_id, type="private")
    msg = Message(
        message_id=1, date=datetime.now(tz=timezone.utc), chat=chat, from_user=user, text=text
    )
    return Update(update_id=1, message=msg)


async def _run(handler, text: str, user_id: int, service) -> str:
    """Call a handler with a fake context; return the last reply text."""
    ctx = FakeContext(service)
    await handler(_update(text, user_id), ctx)
    return ctx.bot.sent[-1][1] if ctx.bot.sent else ""


# --- keeper-bot staff authorization gate (Phase 5 fix: keeper commands were unauthenticated) ---
@pytest.mark.asyncio
async def test_keeper_auth_gate_blocks_strangers_allows_staff_and_owner(monkeypatch):
    from telegram.ext import ApplicationHandlerStop

    from app.db.in_memory import InMemoryTenantRepo

    repo = InMemoryTenantRepo()
    repo.seed_default()
    shop = (await repo.list_shops())[0]
    staff_id = (await repo.list_shopkeepers(shop.id))[0].telegram_id
    monkeypatch.setattr("app.db.factory.get_tenant_repo", lambda: repo)

    def _ctx():
        c = FakeContext(None)
        c.application.bot_data["shop"] = shop
        return c

    # stranger → blocked (stops the handler chain) + told it's staff-only
    stranger = _ctx()
    with pytest.raises(ApplicationHandlerStop):
        await bot._keeper_auth_gate(_update("/exportorders all", 999999), stranger)
    assert "staff only" in stranger.bot.sent[-1][1].lower()

    # registered staff → allowed (no raise, no denial message)
    staff = _ctx()
    await bot._keeper_auth_gate(_update("/profit", staff_id), staff)
    assert staff.bot.sent == []

    # global owner → allowed even if not in this shop's staff list
    owner = _ctx()
    await bot._keeper_auth_gate(_update("/orders", OWNER), owner)
    assert owner.bot.sent == []


# --- owner commands ---
async def test_owner_pause_by_number(tenant_service):
    reply = await _run(bot.pauseshop, "/pauseshop +10000000001 maintenance", OWNER, tenant_service)
    assert "Suspended" in reply and "Shop 01" in reply
    shop = await tenant_service.get_shop_by_whatsapp_number("+10000000001")
    assert shop.status.value == "suspended"


async def test_owner_pause_missing_reason(tenant_service):
    reply = await _run(bot.pauseshop, "/pauseshop +10000000001", OWNER, tenant_service)
    assert "Usage" in reply


async def test_owner_pause_unknown_shop(tenant_service):
    reply = await _run(bot.pauseshop, "/pauseshop +19999999999 reason", OWNER, tenant_service)
    assert "Not found" in reply


async def test_non_owner_blocked(tenant_service):
    reply = await _run(bot.pauseshop, "/pauseshop +10000000001 x", 999, tenant_service)
    assert "Owner only" in reply


async def test_owner_resume(tenant_service):
    await _run(bot.pauseshop, "/pauseshop +10000000001 maintenance", OWNER, tenant_service)
    reply = await _run(bot.resumeshop, "/resumeshop +10000000001", OWNER, tenant_service)
    assert "Resumed" in reply and "active" in reply


async def test_owner_shopstatus(tenant_service):
    reply = await _run(bot.shopstatus, "/shopstatus +10000000003", OWNER, tenant_service)
    assert "Shop 03" in reply and "suspended" in reply


# --- shopkeeper stubs ---
async def test_addproduct_stub(tenant_service):
    reply = await _run(bot._stub("addproduct", 5), "/addproduct", 999, tenant_service)
    assert "not implemented" in reply and "Stage 5" in reply


# --- help ---
async def test_help_owner(tenant_service):
    reply = await _run(bot.help_cmd, "/help", OWNER, tenant_service)
    assert "Owner commands" in reply


async def test_help_non_owner(tenant_service):
    reply = await _run(bot.help_cmd, "/help", 999, tenant_service)
    assert "Shopkeeper commands" in reply


# --- build_application registers handlers ---
async def test_build_application_registers_handlers(tenant_service):
    # needs a token; set via env in conftest already
    app = bot.build_application(tenant_service)
    assert app.bot_data["tenant_service"] is tenant_service
    # count registered CommandHandlers
    assert len(app.handlers.get(0, [])) >= 5  # start, help, 3 owner + stubs


def test_owner_bot_registers_security_commands(tenant_service):
    """SPEC §7/§8 owner commands must be live on the owner control bot, not stubs."""
    from telegram.ext import CommandHandler

    app = bot.build_application(tenant_service)
    names = {c for h in app.handlers.get(0, []) if isinstance(h, CommandHandler) for c in h.commands}
    assert {
        "investigate", "quarantine_extend", "quarantine_lift",
        "blacklist", "forward_to_shop", "bypass_ai", "bypass_remove",
    } <= names


# --- ADR-005: per-shop shopkeeper + customer bots ---
_SHOP_BOTS_ENV = (
    '[{"shop_key":"shop1","keeper_token":"111:AAA","customer_token":"222:BBB","customer_chat_id":100000001},'
    '{"shop_key":"shop2","keeper_token":"333:CCC","customer_token":"444:DDD","customer_chat_id":100000002}]'
)


def test_settings_shop_bots_parses_json(monkeypatch):
    from app.core.config import Settings

    s = Settings(telegram_shop_bots_json=_SHOP_BOTS_ENV)
    bots = s.shop_bots
    assert len(bots) == 2
    assert bots[0]["keeper_token"] == "111:AAA"
    assert bots[1]["customer_chat_id"] == 100000002


def test_settings_shop_bots_empty_when_unset():
    from app.core.config import Settings

    assert Settings(telegram_shop_bots_json="").shop_bots == []


async def test_seed_attaches_shop_bot_tokens(monkeypatch):
    # The settings singleton is bound at import time, so patch its attribute directly
    # rather than the env var (the .env file would otherwise supply real tokens).
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "telegram_shop_bots_json", _SHOP_BOTS_ENV)
    from app.db.in_memory import InMemoryTenantRepo

    repo = InMemoryTenantRepo()
    repo.seed_default()
    shops = await repo.list_shops()
    s1 = next(s for s in shops if s.name.startswith("Shop 01"))
    s2 = next(s for s in shops if s.name.startswith("Shop 02"))
    assert s1.telegram_keeper_bot_token == "111:AAA"
    assert s1.telegram_customer_bot_token == "222:BBB"
    assert s1.telegram_customer_chat_id == 100000001
    assert s2.telegram_keeper_bot_token == "333:CCC"
    assert s2.telegram_customer_chat_id == 100000002


async def test_build_all_applications_yields_owner_plus_per_shop(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "telegram_shop_bots_json", _SHOP_BOTS_ENV)
    from app.db.in_memory import InMemoryTenantRepo
    from app.tenants.service import TenantService

    repo = InMemoryTenantRepo()
    repo.seed_default()
    service = TenantService(repo)
    apps = await bot._build_all_applications(service)
    # 1 owner + 2 shops * (keeper + customer) = 5
    assert len(apps) == 5
    # owner app has no shop in bot_data; shop apps do
    assert "shop" not in apps[0].bot_data
    assert "shop" in apps[1].bot_data


# --- Stage 5: keeper bot gets REAL product commands, not stubs ---
def test_keeper_bot_registers_real_product_commands(tenant_service):
    from uuid import uuid4

    from app.tenants.models import Shop

    from telegram.ext import ConversationHandler

    shop = Shop(id=uuid4(), client_id=uuid4(), name="Shop 01", telegram_keeper_bot_token="111:AAA")
    app = bot.build_shopkeeper_application(tenant_service, shop)
    handlers = app.handlers.get(0, [])
    registered = {cmd: h.callback for h in handlers for cmd in getattr(h, "commands", set())}

    for name, handler in bot.KEEPER_COMMANDS.items():
        assert registered[name] is handler, f"/{name} is stubbed, not wired"

    # /addproduct is the 11-step ConversationHandler, not a plain CommandHandler stub
    convs = [h for h in handlers if isinstance(h, ConversationHandler)]
    assert len(convs) == 1 and convs[0].name == "addproduct"
    assert "addproduct" not in registered

    # commands whose stage hasn't landed are still stubs
    assert {"profit", "exportorders", "productstats"} <= set(registered)
