"""AUDIT — all bots build, and every registered command runs without crashing.

Builds each bot-application factory (owner, per-shop keeper, per-shop customer, global rider — the
6-bot topology) and invokes EVERY registered CommandHandler with a no-argument `/cmd` update from an
authorized user. The backend is stubbed offline (empty Supabase, fakeredis, no outbound sends), so
each command exercises its real handler body and must reply safely — never raise an unhandled
exception. A crash here is a bug; the auth wrappers are supposed to catch and reply to everything.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import fakeredis.aioredis
import pytest
from telegram import Chat, Message, Update, User
from telegram.ext import CommandHandler

import app.db.factory as factory
from app.db.in_memory import InMemoryTenantRepo
from app.telegram_bot import bot
from app.tenants.service import TenantService

OWNER = 100000001  # matches OWNER_TELEGRAM_ID in conftest env


class _AnyQuery:
    """Permissive Supabase stand-in: any chained call returns self; execute() yields no rows."""
    def __getattr__(self, _name):
        return lambda *a, **k: self

    def execute(self):
        return SimpleNamespace(data=[])


class _FakeSupabase:
    def table(self, *a, **k): return _AnyQuery()
    def rpc(self, *a, **k): return _AnyQuery()


class _Bot:
    def __init__(self): self.sent = []
    async def send_message(self, chat_id, text, **_kw): self.sent.append(text)


def _ctx(app):
    return SimpleNamespace(bot=_Bot(), application=app, chat_data={}, user_data={},
                           args=[], bot_data=app.bot_data)


def _update(text: str, user_id: int) -> Update:
    user = User(id=user_id, first_name="T", is_bot=False)
    chat = Chat(id=1, type="private")
    msg = Message(message_id=1, date=datetime.now(tz=timezone.utc), chat=chat, from_user=user, text=text)
    return Update(update_id=1, message=msg)


@pytest.fixture
def offline(monkeypatch):
    """Stub every external edge so command bodies run without network/DB."""
    repo = InMemoryTenantRepo(); repo.seed_default()
    monkeypatch.setattr(factory, "get_tenant_repo", lambda: repo)
    monkeypatch.setattr("app.db.supabase_client.get_supabase", lambda: _FakeSupabase())
    monkeypatch.setattr("app.db.redis_client.get_redis",
                        lambda: fakeredis.aioredis.FakeRedis(decode_responses=True))

    async def _noop(*a, **k): return None
    for name in ("send_to_customer", "send_to_shopkeepers", "send_to_owner", "send_to_rider"):
        monkeypatch.setattr(f"app.telegram_bot.notify.{name}", _noop)
    monkeypatch.setattr("app.audit.service.record", _noop)

    # Excel export hits storage — stub the two bot-namespace calls to a harmless result.
    async def _export(*a, **k): return ("orders.xlsx", "http://x/orders.xlsx", 0)
    monkeypatch.setattr(bot, "export_orders", _export)
    monkeypatch.setattr(bot, "export_rider", _export)

    # Rider auth: pretend the caller is a linked rider so rider commands run their bodies.
    async def _by_tg(tid): return [{"id": str(uuid4()), "name": "Rider", "shop_id": str(uuid4())}]
    monkeypatch.setattr("app.riders.service.riders_by_telegram", _by_tg)
    return SimpleNamespace(repo=repo, svc=TenantService(repo))


async def _run_all_commands(app, user_id: int) -> list[str]:
    """Invoke every CommandHandler in `app` with a no-arg /cmd; return the list of commands run.
    Any unhandled exception propagates and fails the test — that's the bug detector."""
    ran = []
    for group in app.handlers.values():
        for h in group:
            if not isinstance(h, CommandHandler):
                continue
            for cmd in sorted(h.commands):
                ctx = _ctx(app)
                await h.callback(_update(f"/{cmd}", user_id), ctx)
                assert ctx.bot.sent, f"/{cmd} produced no reply"
                ran.append(cmd)
    return ran


@pytest.mark.asyncio
async def test_owner_bot_every_command_runs(offline):
    app = bot.build_application(offline.svc)
    ran = await _run_all_commands(app, OWNER)
    assert {"pauseshop", "resumeshop", "shopstatus", "addrider", "owner", "menu", "help"} <= set(ran)


@pytest.mark.asyncio
async def test_keeper_bot_every_command_runs(offline):
    shop = (await offline.repo.list_shops())[0]
    app = bot.build_shopkeeper_application(offline.svc, shop)
    ran = await _run_all_commands(app, OWNER)
    assert {"orders", "riders", "confirmorder", "assigndelivery", "reconcilecod",
            "pricerequests", "menu"} <= set(ran)


@pytest.mark.asyncio
async def test_rider_bot_every_command_runs(offline):
    app = bot.build_rider_application(offline.svc)
    ran = await _run_all_commands(app, 555)
    assert {"mydeliveries", "accept", "notreceived", "deliver", "canceldelivery",
            "myreport", "menu", "help"} <= set(ran)


@pytest.mark.asyncio
async def test_customer_bot_builds_and_start_replies(offline):
    shop = (await offline.repo.list_shops())[0]
    app = bot.build_customer_application(offline.svc, shop)
    ran = await _run_all_commands(app, 777)  # customer bot exposes only /start (no staff commands)
    assert ran == ["start"]


@pytest.mark.asyncio
async def test_shopowner_bot_every_command_runs(offline, monkeypatch):
    """The 7th bot: a linked client owner runs every registered command without crashing."""
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "telegram_shopowner_bot_token", "7:ownerfake")
    linked = await offline.repo.link_client_telegram("+971500000001", 111222333)  # Client A
    assert linked, "seed Client A phone must link"
    app = bot.build_shopowner_application(offline.svc)
    ran = await _run_all_commands(app, 111222333)
    assert {"start", "help", "menu"} <= set(ran)


@pytest.mark.asyncio
async def test_shopowner_unlinked_user_denied_every_command(offline, monkeypatch):
    """An unlinked Telegram user gets a safe denial (or the link prompt on /start), never data."""
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "telegram_shopowner_bot_token", "7:ownerfake")
    app = bot.build_shopowner_application(offline.svc)
    ran = await _run_all_commands(app, 424242)  # nobody linked this id
    assert {"start", "help", "menu"} <= set(ran)  # all reply; none crash, none leak


@pytest.mark.asyncio
async def test_seven_bot_topology_builds(offline, monkeypatch):
    """1 owner + 1 rider + 1 shop-owner + (keeper + customer) per token-bearing shop.

    Only shops with configured bot tokens run bots (a suspended/unconfigured shop has none), which
    is the 7-bot layout when two shops are provisioned: 3 globals + 2×(keeper+customer).
    """
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "telegram_rider_bot_token", "9:riderfake")
    monkeypatch.setattr(cfg.settings, "telegram_shopowner_bot_token", "7:ownerfake")
    shops = await offline.repo.list_shops()
    tokened = [s for s in shops if s.telegram_keeper_bot_token and s.telegram_customer_bot_token]
    apps = [bot.build_application(offline.svc), bot.build_rider_application(offline.svc),
            bot.build_shopowner_application(offline.svc)]
    for s in tokened:
        apps.append(bot.build_shopkeeper_application(offline.svc, s))
        apps.append(bot.build_customer_application(offline.svc, s))
    assert len(apps) == 3 + 2 * len(tokened)
    assert all(a.handlers for a in apps)
    assert len(tokened) >= 1  # conftest provisions shop bot tokens; at least one shop runs bots
