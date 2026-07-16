"""Telegram bot: owner + shopkeeper commands (SPEC §1, §2, §5, §6, §12).

One module (ethos: fewest files) — split only when it genuinely needs it.
Handlers parse `update.message.text` directly (not PTB's `context.args`) so they
are unit-testable without the PTB dispatcher. Service is injected via
`application.bot_data["tenant_service"]`.

Testing mode = long-polling (ADR-002). WhatsApp is mocked (ADR-002).
"""

from __future__ import annotations

import asyncio
import functools
import logging
import signal
import uuid
from decimal import Decimal
from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from app.telegram_bot import keyboards as kb

from app.escalations.service import DeliveryFailed, NoPendingEscalation
from app.messaging.store import (
    conversations,
    delete_messages,
    format_conversations,
    format_transcript,
    transcript,
)
from app.orders.service import (
    InvalidTransition,
    NoPendingDraft,
    NoPriceRequest,
    OrderNotFound,
    OutOfStock,
    advance_delivery,
    approve_price,
    assign_delivery,
    cancelled_orders,
    confirm_order,
    deny_price,
    discounted_orders,
    export_orders,
    export_rider,
    list_drafts,
    orders_for_export,
    profit_summary,
    reject_order,
    set_negotiation,
)
from app.riders.service import RiderNotFound, add_rider, cod_balance, list_riders
from app.products.addproduct_flow import build_addproduct_handler
from app.reports.service import (
    format_audit_report,
    format_cod_outstanding,
    format_inventory,
    format_owner_profit,
    format_profit,
    format_top_products,
    parse_period,
)
from app.products.service import (
    InvalidBoostLevel,
    InvalidTag,
    ProductNotFound,
    add_tags,
    clear_tags,
    get_product_by_ref,
    list_inventory,
    parse_boost_level,
    parse_price,
    parse_tags,
    remove_tag,
    set_boost,
    toggle_featured,
)
from app.tenants.auth import is_owner
from app.utils.codes import product_code, rider_code
from app.tenants.models import Client, Shop, ShopStatus
from app.tenants.service import ClientNotFound, ShopNotFound, TenantService

logger = logging.getLogger(__name__)


# --- helpers ---
def _service(context: ContextTypes.DEFAULT_TYPE) -> TenantService:
    return context.application.bot_data["tenant_service"]


async def _reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text)


def _split(text: str, *, maxsplit: int = 2) -> list[str]:
    parts = (text or "").split(maxsplit=maxsplit)
    return parts[1:]  # drop the command itself


async def _resolve_shop(service: TenantService, arg: str):
    """Resolve by UUID or whatsapp_number. Raises ShopNotFound if neither matches."""
    try:
        return await service.get_shop(uuid.UUID(arg))
    except (ValueError, ShopNotFound):
        pass
    shop = await service.get_shop_by_whatsapp_number(arg)
    if shop is None:
        raise ShopNotFound(arg)
    return shop


# --- auth decorators ---
def owner_only(handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable]) -> Callable:
    """Gate owner commands. Replies denial to non-owners. Wraps error replies."""

    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user is None or not is_owner(user.id):
            await _reply(update, context, "⛔ Owner only.")
            return
        try:
            await handler(update, context)
        except (ShopNotFound, ClientNotFound) as e:
            await _reply(update, context, f"❌ Not found: {e}")
        except ValueError as e:
            await _reply(update, context, f"⚠️ {e}")
        except Exception:
            logger.exception("owner command failed")
            await _reply(update, context, "❌ Internal error. Owner alerted.")
        else:
            await _audit(update, context, handler.__name__)  # §16: log the privileged action

    return wrapper


# --- owner commands (SPEC §2) ---
@owner_only
async def pauseshop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = _split(update.message.text, maxsplit=2)
    arg = parts[0] if parts else None
    reason = parts[1] if len(parts) > 1 else None
    if not arg or not reason:
        await _reply(update, context, "Usage: /pauseshop <shop_id|number> <reason>")
        return
    service = _service(context)
    shop = await _resolve_shop(service, arg)
    suspended = await service.suspend_shop(shop.id, reason)
    await _reply(update, context, f"✅ Suspended: {suspended.name}\nReason: {suspended.suspension_reason}")


@owner_only
async def resumeshop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = _split(update.message.text, maxsplit=1)
    arg = parts[0] if parts else None
    if not arg:
        await _reply(update, context, "Usage: /resumeshop <shop_id|number>")
        return
    service = _service(context)
    shop = await _resolve_shop(service, arg)
    resumed = await service.resume_shop(shop.id)
    await _reply(update, context, f"✅ Resumed: {resumed.name} ({resumed.status.value})")


@owner_only
async def shopstatus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = _split(update.message.text, maxsplit=1)
    arg = parts[0] if parts else None
    if not arg:
        await _reply(update, context, "Usage: /shopstatus <shop_id|number>")
        return
    service = _service(context)
    shop = await _resolve_shop(service, arg)
    info = await service.shop_status(shop.id)
    keepers = "\n".join(
        f"  - {sk.name or '—'}{' (owner)' if sk.is_owner else ''} [{sk.telegram_id}]"
        for sk in info.shopkeepers
    ) or "  (none)"
    await _reply(
        update,
        context,
        f"🏪 {info.name}\nStatus: {info.status.value}\n"
        f"Reason: {info.suspension_reason or '—'}\nShopkeepers:\n{keepers}",
    )


# --- owner rider onboarding (SPEC §10 delivery) ---
def _rider_ref(r: dict) -> str:
    """What a human types back: the friendly code, or the UUID if 010 hasn't numbered the row yet."""
    n = r.get("rider_number")
    return rider_code(n) if n else str(r["id"])


def _format_riders(shop: Shop, riders: list[dict]) -> str:
    """Shared rider list for owner `/riders <shop>` and keeper `/riders`."""
    if not riders:
        return f"No riders for {shop.name} yet. Owner onboards with /addrider <shop> <phone> <name>."
    lines = [
        f"• {_rider_ref(r)} — {r['name']} — {r['phone']} "
        f"{'🟢 linked' if r.get('telegram_id') else '⚪ not linked'}"
        for r in riders
    ]
    return f"🛵 Riders — {shop.name}\n" + "\n".join(lines) + "\n\n/assigndelivery <order#> <rider001>"


@owner_only
async def addrider(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/addrider <shop_id|number> <phone> <name>` — onboard a delivery rider for a shop."""
    parts = _split(update.message.text, maxsplit=3)
    if len(parts) < 3:
        await _reply(update, context, "Usage: /addrider <shop_id|number> <phone> <name>")
        return
    shop = await _resolve_shop(_service(context), parts[0])
    rider = await add_rider(shop.id, parts[2], parts[1])
    await _reply(
        update, context,
        f"🛵 Rider added to {shop.name}: {rider['name']} ({rider['phone']})\n"
        f"id: {_rider_ref(rider)}\n\n"
        "Ask them to open the rider bot and tap “Share my phone” to start receiving deliveries.",
    )


@owner_only
async def owner_riders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/riders <shop_id|number>` — list a shop's riders (owner view)."""
    parts = _split(update.message.text, maxsplit=1)
    if not parts:
        await _reply(update, context, "Usage: /riders <shop_id|number>")
        return
    shop = await _resolve_shop(_service(context), parts[0])
    await _reply(update, context, _format_riders(shop, await list_riders(shop.id)))


# --- owner security commands (SPEC §7 investigation, §8 bypass) — Stage 7 ---
@owner_only
async def investigate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = _split(update.message.text, maxsplit=1)
    if not parts:
        await _reply(update, context, "Usage: /investigate <incident_id>")
        return
    from app.security.service import get_incident

    inc = await get_incident(parts[0])
    if inc is None:
        await _reply(update, context, "❌ No such incident.")
        return
    await _reply(update, context, _format_incident(inc))


def _format_incident(inc: dict) -> str:
    snap = inc.get("message_snapshot") or []
    lines = "\n".join(f"  {m.get('role', '?')}: {m.get('content', '')[:120]}" for m in snap) or "  (none)"
    return (f"🛡 Incident {inc['id']}\nShop: {inc.get('shop_id')}\nCustomer: {inc['phone']}\n"
            f"Type: {inc['attack_type']}  ·  Status: {inc['status']}\n"
            f"When: {inc['created_at']}\n\nLast {len(snap)} message(s):\n{lines}")


@owner_only
async def quarantine_extend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = _split(update.message.text, maxsplit=1)
    if not parts:
        await _reply(update, context, "Usage: /quarantine_extend <phone>")
        return
    from app.db.redis_client import get_redis
    from app.security.service import extend_quarantine

    await extend_quarantine(get_redis(), parts[0])
    await _reply(update, context, f"⏳ Quarantine extended to 24h for {parts[0]}.")


@owner_only
async def quarantine_lift(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = _split(update.message.text, maxsplit=1)
    if not parts:
        await _reply(update, context, "Usage: /quarantine_lift <phone>")
        return
    from app.db.redis_client import get_redis
    from app.security.service import lift_quarantine

    await lift_quarantine(get_redis(), parts[0])
    await _reply(update, context, f"✅ Quarantine lifted for {parts[0]}.")


@owner_only
async def blacklist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = _split(update.message.text, maxsplit=1)
    if not parts:
        await _reply(update, context, "Usage: /blacklist <phone> [reason]")
        return
    from app.db.redis_client import get_redis
    from app.security.service import blacklist

    phone = parts[0]
    reason = parts[1] if len(parts) > 1 else "owner blacklist"
    await blacklist(get_redis(), phone, None, reason)
    await _reply(update, context, f"⛔ {phone} blacklisted. Their messages are now silently ignored.")


@owner_only
async def forward_to_shop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = _split(update.message.text, maxsplit=2)
    if len(parts) < 2:
        await _reply(update, context, "Usage: /forward_to_shop <phone> <shop_id|number>")
        return
    from app.db.redis_client import get_redis
    from app.security.service import forward_to_shop as _forward

    shop = await _resolve_shop(_service(context), parts[1])  # validate the shop exists
    await _forward(get_redis(), parts[0])
    await _reply(update, context, f"➡️ {parts[0]} now routed straight to {shop.name}'s staff (quarantine lifted).")


@owner_only
async def bypass_ai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = _split(update.message.text, maxsplit=2)
    if not parts:
        await _reply(update, context, "Usage: /bypass_ai <phone> [shop_id] [reason]")
        return
    from app.db.redis_client import get_redis
    from app.security.service import set_bypass

    await set_bypass(get_redis(), parts[0])
    await _reply(update, context, f"➡️ {parts[0]} bypasses the AI — messages go straight to the shop's staff.")


@owner_only
async def bypass_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = _split(update.message.text, maxsplit=1)
    if not parts:
        await _reply(update, context, "Usage: /bypass_remove <phone>")
        return
    from app.db.redis_client import get_redis
    from app.security.service import remove_bypass

    await remove_bypass(get_redis(), parts[0])
    await _reply(update, context, f"✅ Bypass removed for {parts[0]} — the AI handles them again.")


OWNER_SECURITY_COMMANDS: dict[str, Callable] = {
    "investigate": investigate,
    "quarantine_extend": quarantine_extend,
    "quarantine_lift": quarantine_lift,
    "blacklist": blacklist_cmd,
    "forward_to_shop": forward_to_shop,
    "bypass_ai": bypass_ai,
    "bypass_remove": bypass_remove,
}


# --- owner dispatcher (SPEC §6 profit + §12 dashboards) — Stage 8/10 ---
@owner_only
async def owner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/owner <dashboard|health|profit|escalations|security|audit> …` (SPEC §6, §12)."""
    args = _split(update.message.text, maxsplit=3)
    sub = args[0].lower() if args else ""
    rest = args[1:]
    if sub == "profit":
        return await _owner_profit(update, context, rest)
    if sub == "dashboard":
        return await _owner_dashboard(update, context)
    if sub == "health":
        return await _owner_health(update, context)
    if sub == "escalations":
        return await _owner_escalations(update, context)
    if sub == "security":
        return await _owner_security(update, context)
    if sub == "audit":
        return await _owner_audit(update, context)
    await _reply(
        update, context,
        "Usage: /owner <cmd>\n"
        "dashboard · health · escalations · security · audit\n"
        "profit [all|compare|shop <id>] [period]",
    )


async def _owner_profit(update: Update, context: ContextTypes.DEFAULT_TYPE, rest: list[str]) -> None:
    """`/owner profit [all|compare|shop <id>] [period]` — profit across shops (SPEC §6)."""
    service = _service(context)
    mode = rest[0] if rest and rest[0] in ("all", "compare", "shop") else None

    if mode == "shop":
        if len(rest) < 2:
            await _reply(update, context, "Usage: /owner profit shop <id|number> [period]")
            return
        shop = await _resolve_shop(service, rest[1])
        start, end, label = parse_period(rest[2] if len(rest) > 2 else "today")
        s = await profit_summary(shop.id, start, end)
        await _reply(update, context, format_profit(s, f"{shop.name} — {label}"))
        return

    period = rest[1] if mode and len(rest) > 1 else (rest[0] if rest and not mode else "today")
    start, end, label = parse_period(period)
    items = [(sh.name, await profit_summary(sh.id, start, end)) for sh in await service.list_shops()]
    await _reply(update, context, format_owner_profit(items, label))


async def _owner_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/owner health` — full subsystem health (§13), same checker `/health` uses."""
    from app.db.factory import get_tenant_repo
    from app.db.redis_client import get_redis
    from app.reports.health import check_health, format_health

    report = await check_health(get_redis(), get_tenant_repo())
    await _reply(update, context, format_health(report))


async def _owner_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/owner dashboard` — one-screen ops snapshot for today (§12)."""
    from app.db.factory import get_tenant_repo
    from app.db.redis_client import get_redis
    from app.escalations.service import count_open
    from app.reports.health import check_health

    service = _service(context)
    shops = await service.list_shops()
    active = sum(1 for s in shops if s.status.value == "active")
    start, end, label = parse_period("today")
    total = 0.0
    for s in shops:
        total += float((await profit_summary(s.id, start, end)).profit)
    report = await check_health(get_redis(), get_tenant_repo())
    open_esc = await count_open()
    await _reply(
        update, context,
        f"🏢 Owner Dashboard — {label}\n\n"
        f"Shops: {len(shops)} · {active} active · {len(shops) - active} paused\n"
        f"Profit today: +{total:,.0f} AED (all shops)\n"
        f"Open escalations: {open_esc}\n\n"
        f"Health: {'🟢 healthy' if report.ok else '🔴 UNHEALTHY'}\n"
        f"  active chats: {report.metrics.get('active_conversations', 0)} · "
        f"quarantined: {report.metrics.get('quarantined', 0)}",
    )


async def _owner_escalations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/owner escalations` — open escalations across all shops (§12)."""
    from app.escalations.service import list_open

    rows = await list_open()
    if not rows:
        await _reply(update, context, "No open escalations. 🎉")
        return
    lines = [f"• {r['phone']} — {(r.get('message') or '')[:50]} ({(r.get('created_at') or '')[:16]})"
             for r in rows]
    await _reply(update, context, "Open escalations:\n" + "\n".join(lines))


async def _owner_security(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/owner security` — recent security incidents (§12); `/investigate <id>` for detail."""
    from app.security.service import recent_incidents

    rows = await recent_incidents()
    if not rows:
        await _reply(update, context, "No security incidents recorded.")
        return
    lines = [f"• {r['id'][:8]} {r['attack_type']} — {r['phone']} [{r['status']}] "
             f"({(r.get('created_at') or '')[:16]})" for r in rows]
    await _reply(update, context, "Recent security incidents:\n" + "\n".join(lines)
                 + "\n\n/investigate <id> for the full snapshot")


async def _owner_audit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/owner audit` — recent privileged actions from `audit_logs` (§16)."""
    from app.audit.service import recent

    rows = await recent()
    if not rows:
        await _reply(update, context, "No audit entries yet.")
        return
    lines = [f"• {(r.get('created_at') or '')[:16]} — {r['actor']} → {r['action']}" for r in rows]
    await _reply(update, context, "Recent activity:\n" + "\n".join(lines))


# --- common ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user and is_owner(user.id):
        await context.bot.send_message(update.effective_chat.id, "👑 Owner controls — tap or /help",
                                       reply_markup=kb.owner_menu())
        return
    await _reply(update, context, "Multi-shop chatbot. Owner: /help. Shopkeepers: /help.")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user and is_owner(user.id):
        await _reply(
            update,
            context,
            "👉 /menu — do everything with buttons (no typing commands)\n\n"
            "Owner commands:\n/pauseshop <id|num> <reason>\n/resumeshop <id|num>\n"
            "/shopstatus <id|num>\n\n"
            "Riders (§10):\n/addrider <shop> <phone> <name>\n/riders <shop>\n\n"
            "Dashboards (§12):\n/owner dashboard · /owner health\n"
            "/owner escalations · /owner security · /owner audit\n\n"
            "Profit (§6):\n/owner profit [all|compare|shop <id>] [period]\n\n"
            "Security (§7/§8):\n/investigate <incident_id>\n"
            "/quarantine_lift <phone> · /quarantine_extend <phone>\n"
            "/blacklist <phone> [reason]\n/forward_to_shop <phone> <shop_id>\n"
            "/bypass_ai <phone> · /bypass_remove <phone>",
        )
    else:
        await _reply(
            update,
            context,
            "Shopkeeper commands (Stages 5–9):\n/addproduct · /boost · /tag · /feature\n"
            "/profit today · /exportorders today\n(replies 'not implemented' until then)",
        )


# --- shopkeeper stubs (SPEC §5/§6/§12) — real impl in Stages 5–9 ---
def _stub(name: str, stage: int) -> Callable:
    async def _h(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await _reply(update, context, f"⏳ /{name} — not implemented yet (Stage {stage}).")

    _h.__name__ = name
    return _h


SHOPKEEPER_STUBS: dict[str, tuple[int, str]] = {
    # §4/§5 products
    "addproduct": (5, "addproduct"),
    "boost": (5, "boost"),
    "unboost": (5, "unboost"),
    "tag": (5, "tag"),
    "untag": (5, "untag"),
    "cleartags": (5, "cleartags"),
    "feature": (5, "feature"),
    # §6 profit
    "profit": (8, "profit"),
    # §10 export
    "exportorders": (9, "exportorders"),
    "exportrider": (9, "exportrider"),
    # §12 reports
    "report": (8, "report"),
    # §3 escalation
    "reply": (6, "reply"),
    "handover": (6, "handover"),
}


# --- shopkeeper product commands (SPEC §5) — Stage 5 ---
def _shop_of(context: ContextTypes.DEFAULT_TYPE) -> Shop:
    return context.application.bot_data["shop"]


async def _resolve_product(shop: Shop, raw: str) -> uuid.UUID:
    """The single place a keeper-typed product reference becomes a UUID — a full UUID or a
    friendly code ('PR0001'). Every product command routes through here (SPEC §5). Raises
    ProductNotFound on miss (mapped to the safe reply; never confirms a foreign product)."""
    product = await get_product_by_ref(shop.id, raw)
    return product.id


async def _keeper_err(update: Update, context: ContextTypes.DEFAULT_TYPE, exc: Exception) -> None:
    """Map a shopkeeper-action exception to a safe reply (shared by commands and buttons)."""
    if isinstance(exc, ProductNotFound):
        # Same message whether the id is unknown or owned by another shop —
        # never confirm that another shop's product exists.
        await _reply(update, context, "❌ No such product in this shop.")
    elif isinstance(exc, NoPendingEscalation):
        await _reply(update, context, f"❌ {exc} is not waiting for a reply from this shop.")
    elif isinstance(exc, NoPendingDraft):
        await _reply(update, context, f"❌ No pending order draft #{exc} for this shop.")
    elif isinstance(exc, NoPriceRequest):
        await _reply(update, context, f"❌ No pending price request #{exc} for this shop.")
    elif isinstance(exc, OutOfStock):
        await _reply(update, context, f"⚠️ Order #{exc} can't be confirmed — that item is now out of stock.")
    elif isinstance(exc, OrderNotFound):
        await _reply(update, context, f"❌ No order #{exc} in this shop.")
    elif isinstance(exc, RiderNotFound):
        await _reply(update, context, "❌ No such rider in this shop. /riders to see them.")
    elif isinstance(exc, (InvalidTransition, DeliveryFailed)):
        verb = "deliver to" if isinstance(exc, DeliveryFailed) else ""
        await _reply(update, context,
                     f"❌ Could not {verb} {exc}. They may have blocked the bot." if verb else f"⚠️ {exc}")
    elif isinstance(exc, (InvalidBoostLevel, InvalidTag, ValueError)):
        await _reply(update, context, f"⚠️ {exc}")
    else:
        logger.exception("keeper action failed", exc_info=exc)
        await _reply(update, context, "❌ Internal error.")


def keeper_command(handler: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable]) -> Callable:
    """Wrap a shopkeeper command: typed errors → safe replies (CONVENTIONS)."""

    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            await handler(update, context)
        except Exception as e:  # mapped to a safe reply; never leak internals
            await _keeper_err(update, context, e)
        else:
            await _audit(update, context, handler.__name__)  # §16: log the shop action

    return wrapper


async def _audit(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str) -> None:
    """Append an audit row for a completed privileged command (§16). Best-effort — never raises."""
    from app.audit.service import record

    user = update.effective_user
    shop = context.application.bot_data.get("shop")  # present on keeper bots, absent on owner control bot
    text = (update.message.text or "")[:300] if update.message else ""
    await record(
        str(user.id) if user else "unknown", action,
        shop_id=shop.id if shop else None, detail={"text": text},
    )


@keeper_command
async def boost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = _split(update.message.text, maxsplit=2)
    if len(parts) < 2:
        await _reply(update, context, "Usage: /boost <product> <1-10>")
        return
    shop = _shop_of(context)
    p = await set_boost(shop.id, await _resolve_product(shop, parts[0]), parse_boost_level(parts[1]))
    await _reply(update, context, f"🚀 {p.brand} {p.model} — boost {p.boost_level}/10.")


@keeper_command
async def unboost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = _split(update.message.text, maxsplit=1)
    if not parts:
        await _reply(update, context, "Usage: /unboost <product>")
        return
    shop = _shop_of(context)
    p = await set_boost(shop.id, await _resolve_product(shop, parts[0]), 0)  # /unboost = boost 0
    await _reply(update, context, f"✅ {p.brand} {p.model} — boost cleared.")


@keeper_command
async def tag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = _split(update.message.text, maxsplit=2)
    if len(parts) < 2:
        await _reply(update, context, "Usage: /tag <product> <tag1,tag2>")
        return
    shop = _shop_of(context)
    p = await add_tags(shop.id, await _resolve_product(shop, parts[0]), parse_tags(parts[1]))
    await _reply(update, context, f"🏷 {p.brand} {p.model} — tags: {', '.join(p.tags) or '—'}")


@keeper_command
async def untag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = _split(update.message.text, maxsplit=2)
    if len(parts) < 2:
        await _reply(update, context, "Usage: /untag <product> <tag>")
        return
    shop = _shop_of(context)
    p = await remove_tag(shop.id, await _resolve_product(shop, parts[0]), parts[1])
    await _reply(update, context, f"🏷 {p.brand} {p.model} — tags: {', '.join(p.tags) or '—'}")


@keeper_command
async def cleartags(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = _split(update.message.text, maxsplit=1)
    if not parts:
        await _reply(update, context, "Usage: /cleartags <product>")
        return
    shop = _shop_of(context)
    p = await clear_tags(shop.id, await _resolve_product(shop, parts[0]))
    await _reply(update, context, f"🏷 {p.brand} {p.model} — all tags removed.")


@keeper_command
async def feature(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = _split(update.message.text, maxsplit=1)
    if not parts:
        await _reply(update, context, "Usage: /feature <product>")
        return
    shop = _shop_of(context)
    p = await toggle_featured(shop.id, await _resolve_product(shop, parts[0]))
    await _reply(update, context, f"{'⭐' if p.is_featured else '☆'} {p.brand} {p.model} — featured: {p.is_featured}")


# --- shopkeeper profit (SPEC §6) — Stage 8 ---
@keeper_command
async def profit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/profit [today|yesterday|weekly|monthly|YYYY-MM-DD]` — this shop's profit (SPEC §6)."""
    parts = _split(update.message.text, maxsplit=1)
    start, end, label = parse_period(parts[0] if parts else "today")
    s = await profit_summary(_shop_of(context).id, start, end)
    await _reply(update, context, format_profit(s, label))


# --- shopkeeper order confirmation (Q-017 hybrid booking) — Stage 8 ---
def _order_number(raw: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"'{raw}' is not an order number")


@keeper_command
async def orders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/orders` — list order drafts awaiting confirmation."""
    drafts = await list_drafts(_shop_of(context).id)
    if not drafts:
        await _reply(update, context, "No pending order drafts.")
        return
    await _reply(update, context, "📥 Pending order drafts:")
    for d in drafts:
        p = d.get("products") or {}
        net = float(d["selling_price"]) - float(d["discount_amount"])
        text = (f"#{d['order_number']} — {d['customer_name']} — "
                f"{p.get('brand', '')} {p.get('model', '')} ×{d['quantity']} — {net:.0f} AED")
        await context.bot.send_message(update.effective_chat.id, text,
                                       reply_markup=kb.keeper_order_actions(d["order_number"]))


@keeper_command
async def confirmorder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/confirmorder <#>` — accept a draft: decrement stock + notify the customer."""
    parts = _split(update.message.text, maxsplit=1)
    if not parts:
        await _reply(update, context, "Usage: /confirmorder <order_number>")
        return
    num = _order_number(parts[0])
    await confirm_order(_shop_of(context), num)
    await _reply(update, context, f"✅ Order #{num} confirmed. Stock updated, customer notified.")


@keeper_command
async def rejectorder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/rejectorder <#> [reason]` — decline a draft. The customer is not cold-messaged."""
    parts = _split(update.message.text, maxsplit=2)
    if not parts:
        await _reply(update, context, "Usage: /rejectorder <order_number> [reason]")
        return
    num = _order_number(parts[0])
    await reject_order(_shop_of(context), num, parts[1] if len(parts) > 1 else None)
    await _reply(update, context, f"❌ Order #{num} rejected. Use /reply {num} … if you want to tell the customer.")


@keeper_command
async def deliveryupdate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/deliveryupdate <#> packed|shipped|delivered` — advance fulfilment one step; customer is told."""
    parts = _split(update.message.text, maxsplit=2)
    if len(parts) < 2:
        await _reply(update, context, "Usage: /deliveryupdate <order_number> packed|shipped|delivered")
        return
    num = _order_number(parts[0])
    await advance_delivery(_shop_of(context), num, parts[1])
    await _reply(update, context, f"🚚 Order #{num} → {parts[1].lower()}. Customer notified.")


# --- shopkeeper rider assignment (SPEC §10 delivery) ---
@keeper_command
async def riders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/riders` — this shop's riders + their ids (for /assigndelivery)."""
    shop = _shop_of(context)
    await _reply(update, context, _format_riders(shop, await list_riders(shop.id)))


@keeper_command
async def assigndelivery_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/assigndelivery <order#> <rider>` — attach a rider to a confirmed order + notify them."""
    parts = _split(update.message.text, maxsplit=2)
    if len(parts) < 2:
        await _reply(update, context, "Usage: /assigndelivery <order_number> <rider|rider001|name>")
        return
    num = _order_number(parts[0])
    shop = _shop_of(context)
    rider_id = uuid.UUID(str((await _resolve_rider(shop, parts[1]))["id"]))
    res = await assign_delivery(shop, num, rider_id)
    tail = ("Rider notified. 📲" if res["notified"]
            else "⚠️ Rider hasn't linked Telegram yet — ask them to /start the rider bot.")
    await _reply(update, context,
                 f"🛵 Order #{num} → {res['rider']['name']} (COD {res['cod']} AED). {tail}")


async def _resolve_rider(shop: Shop, arg: str) -> dict:
    """Rider by UUID, friendly code ('rider001') or case-insensitive name, within this shop.

    The single place a keeper-typed rider reference is resolved (/reconcilecod, /assigndelivery,
    /exportrider). RiderNotFound on miss — same message for unknown and another shop's rider."""
    from app.riders.service import get_rider, list_riders

    from app.utils.codes import parse_rider_code

    arg = (arg or "").strip()
    try:
        return await get_rider(shop.id, uuid.UUID(arg))
    except ValueError:
        pass  # not a uuid — try a friendly code, then a name

    number = parse_rider_code(arg)
    if number is not None:
        by_code = [r for r in await list_riders(shop.id) if r.get("rider_number") == number]
        if by_code:
            return by_code[0]
        raise RiderNotFound(arg)

    matches = [r for r in await list_riders(shop.id) if (r.get("name") or "").lower() == arg.lower()]
    if not matches:
        raise RiderNotFound(arg)
    if len(matches) > 1:
        raise ValueError(f"{len(matches)} riders named '{arg}' — use the rider id from /riders")
    return matches[0]


@keeper_command
async def reconcilecod_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/reconcilecod <rider_id|name> <amount>` — end-of-day cash handover. Shows the full trail
    (previous balance + today COD − handed over = remaining) and pushes the same trail to the rider."""
    from app.riders.service import parse_cash, reconcile_cod

    parts = _split(update.message.text, maxsplit=2)
    if len(parts) < 2:
        await _reply(update, context, "Usage: /reconcilecod <rider_id|name> <amount_received>")
        return
    shop = _shop_of(context)
    rider = await _resolve_rider(shop, parts[0])
    trail = await reconcile_cod(shop, rider, parse_cash(parts[1]))
    tail = ("\n\n📲 Trail sent to the rider." if rider.get("telegram_id")
            else "\n\n⚠️ Rider hasn't linked Telegram — trail not pushed.")
    await _reply(update, context, trail["text"] + tail)


# --- shopkeeper price negotiation (ADR-010 rev.) — Stage 8 ---
@keeper_command
async def negotiation_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/negotiation on|off` — allow or forbid the AI to raise price requests for this shop."""
    parts = _split(update.message.text, maxsplit=1)
    choice = (parts[0].lower() if parts else "")
    if choice not in ("on", "off"):
        await _reply(update, context, "Usage: /negotiation on|off")
        return
    await set_negotiation(_shop_of(context).id, choice == "on")
    await _reply(
        update, context,
        "💬 Negotiation ON — the assistant may ask you to approve discounts." if choice == "on"
        else "🔒 Negotiation OFF — the assistant will hold every price at list; no discounts.",
    )


@keeper_command
async def approveprice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/approveprice <#>` — accept the customer's requested price. Customer is told."""
    parts = _split(update.message.text, maxsplit=1)
    if not parts:
        await _reply(update, context, "Usage: /approveprice <request_number>")
        return
    price = await approve_price(_shop_of(context), _order_number(parts[0]))
    await _reply(update, context, f"✅ Approved at {price} AED. Customer notified.")


@keeper_command
async def custom_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/custom <#> <price>` — counter with your own price. Customer is told."""
    parts = _split(update.message.text, maxsplit=2)
    if len(parts) < 2:
        await _reply(update, context, "Usage: /custom <request_number> <price>")
        return
    price = await approve_price(_shop_of(context), _order_number(parts[0]), parse_price(parts[1]))
    await _reply(update, context, f"✅ Offered {price} AED to the customer.")


@keeper_command
async def denyprice_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/denyprice <#>` — decline. Customer told the list price is the best available."""
    parts = _split(update.message.text, maxsplit=1)
    if not parts:
        await _reply(update, context, "Usage: /denyprice <request_number>")
        return
    await deny_price(_shop_of(context), _order_number(parts[0]))
    await _reply(update, context, "❌ Declined. Customer told the list price stands.")


@keeper_command
async def pricerequests_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/pricerequests` — pending haggles, each with Approve/Counter/Decline buttons."""
    from app.orders.service import list_price_requests

    reqs = await list_price_requests(_shop_of(context).id)
    if not reqs:
        await _reply(update, context, "No pending price requests.")
        return
    await _reply(update, context, "💰 Pending price requests:")
    for r in reqs:
        p = r.get("products") or {}
        text = (f"#{r['request_number']} — {r['phone']} — {p.get('brand', '')} {p.get('model', '')}\n"
                f"Offer {r['requested_price']} AED · List {p.get('selling_price', '?')} AED")
        await context.bot.send_message(update.effective_chat.id, text,
                                       reply_markup=kb.keeper_price_actions(r["request_number"]))


# --- shopkeeper Excel export (SPEC §10) — Stage 9 ---
@keeper_command
async def exportorders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/exportorders [today|yesterday|YYYY-MM-DD|pending|all] [detailed]` — pick-&-pack .xlsx."""
    parts = _split(update.message.text, maxsplit=2)
    filt = parts[0] if parts else "today"
    detailed = any(p.lower() == "detailed" for p in parts[1:])
    if filt.lower() == "detailed":  # `/exportorders detailed` → today, detailed
        filt, detailed = "today", True
    name, url, n = await export_orders(_shop_of(context), filt, detailed)
    await _reply(update, context, f"📄 {n} order(s) — {name}\n{url}\n(link valid 24h)")


@keeper_command
async def exportrider_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/exportrider <rider> [today|yesterday|YYYY-MM-DD]` — one rider's route, sorted by address."""
    parts = _split(update.message.text, maxsplit=2)
    if not parts:
        await _reply(update, context, "Usage: /exportrider <rider|rider001|name> [today|yesterday|YYYY-MM-DD]")
        return
    shop = _shop_of(context)
    rider = uuid.UUID(str((await _resolve_rider(shop, parts[0]))["id"]))
    name, url, n = await export_rider(shop, rider, parts[1] if len(parts) > 1 else "today")
    await _reply(update, context, f"🛵 {n} order(s) for rider — {name}\n{url}\n(link valid 24h)")


# --- counter sale sheet: the printable day sheet the shop fills by hand ---
@keeper_command
async def countersheet_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/countersheet` — Excel of the catalogue with empty 'Price sold' / 'Qty sold' columns.
    Print it, fill it at the counter, then the shop owner photographs it into 🧾 Today sell."""
    from app.products.service import export_counter_sheet

    shop = _shop_of(context)
    name, url, n = await export_counter_sheet(shop)
    await _reply(
        update, context,
        f"🧾 Counter sheet — {n} product(s)\n{url}\n(link valid 24h)\n\n"
        "Print it, record each counter sale by hand, then the shop owner uploads a photo "
        "of the filled sheet from their bot.",
    )


# --- shopkeeper product stats (SPEC §5; Q-014) ---
@keeper_command
async def productstats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/productstats [today|yesterday|weekly|monthly|YYYY-MM-DD]` — what sold, what didn't."""
    args = _split(update.message.text, maxsplit=1) if update.message else []
    await _do_product_stats(update, context, args[0] if args else "")


async def _do_product_stats(update: Update, context: ContextTypes.DEFAULT_TYPE, arg: str) -> None:
    """Shared by /productstats and the stats-period buttons."""
    from app.orders.service import product_stats
    from app.reports.service import format_product_stats

    shop = _shop_of(context)
    start, end, label = parse_period(arg)
    rows = await product_stats(shop.id, start, end)
    await _reply(update, context, format_product_stats(shop.name, rows, label))


# --- shopkeeper escalation commands (SPEC §3) — Stage 6 ---
@keeper_command
async def reply_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/reply <customer> <text>` — answer an escalated customer, in the shop's voice."""
    parts = _split(update.message.text, maxsplit=2)
    if len(parts) < 2 or not parts[1].strip():
        await _reply(update, context, "Usage: /reply <customer> <your message>")
        return
    from app.db.redis_client import get_redis
    from app.escalations.service import reply as send_reply

    shop = _shop_of(context)
    await send_reply(get_redis(), shop, parts[0], parts[1].strip())
    await _reply(update, context, f"✅ Sent to {parts[0]}.")


@keeper_command
async def handover_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/handover <customer>` — give the conversation back to the assistant (SPEC §3)."""
    parts = _split(update.message.text, maxsplit=1)
    if not parts:
        await _reply(update, context, "Usage: /handover <customer>")
        return
    from app.db.redis_client import get_redis
    from app.escalations.service import handover

    shop = _shop_of(context)
    await handover(get_redis(), shop, parts[0])
    await _reply(
        update,
        context,
        f"✅ {parts[0]} handed back to the assistant. It has the full conversation.",
    )


# Real handlers replace the stubs on the per-shop keeper bot (they need bot_data["shop"]).
KEEPER_COMMANDS: dict[str, Callable] = {
    "boost": boost,
    "unboost": unboost,
    "tag": tag,
    "untag": untag,
    "cleartags": cleartags,
    "feature": feature,
    # SPEC §3 escalation (Stage 6)
    "reply": reply_cmd,
    "handover": handover_cmd,
    # SPEC §6 profit (Stage 8)
    "profit": profit_cmd,
    # Q-017 hybrid booking (Stage 8)
    "orders": orders_cmd,
    "confirmorder": confirmorder_cmd,
    "rejectorder": rejectorder_cmd,
    "deliveryupdate": deliveryupdate_cmd,
    # SPEC §10 rider assignment + COD
    "riders": riders_cmd,
    "assigndelivery": assigndelivery_cmd,
    "reconcilecod": reconcilecod_cmd,
    # ADR-010 rev. price negotiation (Stage 8)
    "negotiation": negotiation_cmd,
    "approveprice": approveprice_cmd,
    "custom": custom_cmd,
    "denyprice": denyprice_cmd,
    "pricerequests": pricerequests_cmd,
    # SPEC §10 Excel export (Stage 9)
    "exportorders": exportorders_cmd,
    "exportrider": exportrider_cmd,
    "countersheet": countersheet_cmd,
    # SPEC §5 product stats (Q-014)
    "productstats": productstats_cmd,
}

# `/addproduct` is a ConversationHandler, not a plain CommandHandler — registered separately.
KEEPER_REAL_COMMANDS: frozenset[str] = frozenset({*KEEPER_COMMANDS, "addproduct"})


# --- application factory: OWNER control bot (ADR-005) ---
async def _log_inbound(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Low-priority inbound logger so messages are visible in the runner log."""
    u = update.effective_user
    m = update.message
    if m is None:
        return
    logger.info("inbound bot=%s user=%s chat=%s text=%r",
                context.bot.username, u.id if u else None,
                update.effective_chat.id, (m.text or "")[:200])


async def owner_menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/menu` — the owner's button menu."""
    user = update.effective_user
    if not (user and is_owner(user.id)):
        await _reply(update, context, "⛔ Owner only.")
        return
    await context.bot.send_message(update.effective_chat.id, "👑 Owner controls",
                                   reply_markup=kb.owner_menu())


# Security button → the prompt shown before the owner types the value.
_OSEC_PROMPT = {
    "oinv": "🔎 Reply with the incident id.", "oblk": "🚫 Reply with:  <phone> [reason]",
    "oqlift": "🔓 Reply with the phone to lift.", "oqext": "⏲ Reply with the phone to extend.",
    "ofwd": "📨 Reply with:  <phone> <shop_id|number>", "obyp": "🤖 Reply with the phone to bypass.",
    "obypr": "↩️ Reply with the phone to un-bypass.",
}


async def _owner_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-button dispatcher for the owner bot."""
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    if not (user and is_owner(user.id)):
        await _reply(update, context, "⛔ Owner only.")
        return
    service = _service(context)
    action, args = kb.parse_cb(q.data)
    try:
        if action == "omenu":
            await q.edit_message_text("👑 Owner controls", reply_markup=kb.owner_menu())
        elif action == "oshops":
            shops = await service.list_shops()
            if not shops:
                await _reply(update, context, "No shops yet.")
            else:
                picker = [{"id": str(s.id), "name": s.name} for s in shops]
                await q.edit_message_text("🏪 Which shop?", reply_markup=kb.owner_shop_picker(picker))
        elif action == "oshop":
            await q.edit_message_text("🏪 Shop actions:", reply_markup=kb.owner_shop_actions(args[0]))
        elif action == "odash":
            await _owner_dashboard(update, context)
        elif action == "ohealth":
            await _owner_health(update, context)
        elif action == "oesc":
            await _owner_escalations(update, context)
        elif action == "oaudit":
            await _owner_audit(update, context)
        elif action == "osecmenu":
            await q.edit_message_text("🛡 Security:", reply_markup=kb.owner_security_menu())
        elif action == "oprofmenu":
            await q.edit_message_text("💰 Profit for…", reply_markup=kb.owner_profit_menu())
        elif action == "oprof":
            await _owner_profit(update, context, args)  # ['today'|…|'compare']
        elif action == "oresume":
            resumed = await service.resume_shop(uuid.UUID(args[0]))
            await _reply(update, context, f"✅ Resumed: {resumed.name} ({resumed.status.value})")
        elif action == "ostatus":
            info = await service.shop_status(uuid.UUID(args[0]))
            keepers = "\n".join(f"  - {sk.name or '—'}{' (owner)' if sk.is_owner else ''} [{sk.telegram_id}]"
                                for sk in info.shopkeepers) or "  (none)"
            await _reply(update, context, f"🏪 {info.name}\nStatus: {info.status.value}\n"
                         f"Reason: {info.suspension_reason or '—'}\nShopkeepers:\n{keepers}")
        elif action == "oriders":
            shop = await service.get_shop(uuid.UUID(args[0]))
            await _reply(update, context, _format_riders(shop, await list_riders(shop.id)))
        elif action == "opause":
            context.chat_data["pending"] = {"do": "opause", "args": args}
            await _reply(update, context, "⏸ Reply with the suspension reason.")
        elif action == "oaddr":
            context.chat_data["pending"] = {"do": "oaddr", "args": args}
            await _reply(update, context, "➕ Reply with:  <phone> <name>")
        elif action == "omsgmenu":
            await q.edit_message_text("🧹 Messages (permanent chat archive):",
                                      reply_markup=kb.owner_messages_menu())
        elif action == "omdelshop":
            shops = await service.list_shops()
            picker = [{"id": str(s.id), "name": s.name} for s in shops]
            await q.edit_message_text("🏪 Delete messages of which shop?",
                                      reply_markup=kb.owner_msg_shop_picker(picker))
        elif action == "omdel":
            context.chat_data["pending"] = {"do": "omdel", "args": args}
            if args[0] == "range":
                await _reply(update, context, "🗓 Reply with:  YYYY-MM-DD YYYY-MM-DD  (inclusive)")
            elif args[0] == "shop":
                await _reply(update, context,
                             "⚠️ Reply YES to permanently delete ALL messages of this shop.")
            else:
                await _reply(update, context, "⚠️ Reply YES to permanently delete ALL chat messages.")
        elif action in _OSEC_PROMPT:
            context.chat_data["pending"] = {"do": action, "args": []}
            await _reply(update, context, _OSEC_PROMPT[action])
        else:
            await _reply(update, context, "Unknown action. /menu")
    except (ShopNotFound, ClientNotFound) as e:
        await _reply(update, context, f"❌ Not found: {e}")
    except ValueError as e:
        await _reply(update, context, f"⚠️ {e}")
    except Exception:
        logger.exception("owner button failed")
        await _reply(update, context, "❌ Internal error.")


async def _owner_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Consume a free-text reply an owner button asked for (pause reason, add-rider, security value)."""
    pending = context.chat_data.get("pending")
    user = update.effective_user
    if not pending or not (user and is_owner(user.id)):
        return
    context.chat_data.pop("pending", None)
    service = _service(context)
    do, args = pending["do"], pending["args"]
    text = (update.message.text or "").strip()
    try:
        if do == "opause":
            if not text:
                await _reply(update, context, "A reason is required. Tap ⏸ Pause again.")
                return
            s = await service.suspend_shop(uuid.UUID(args[0]), text)
            await _reply(update, context, f"✅ Suspended: {s.name}\nReason: {s.suspension_reason}")
        elif do == "oaddr":
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await _reply(update, context, "Need <phone> <name>. Tap ➕ Add rider again.")
                return
            shop = await service.get_shop(uuid.UUID(args[0]))
            rider = await add_rider(shop.id, parts[1], parts[0])
            await _reply(update, context, f"🛵 Rider added to {shop.name}: {rider['name']} ({rider['phone']})\n"
                         f"id: {_rider_ref(rider)}\nAsk them to open the rider bot and tap “Share my phone”.")
        elif do == "omdel":
            await _owner_delete_messages(update, context, args, text)
        else:
            await _owner_security_text(update, context, do, text)
    except (ShopNotFound, ClientNotFound) as e:
        await _reply(update, context, f"❌ Not found: {e}")
    except ValueError as e:
        await _reply(update, context, f"⚠️ {e}")
    except Exception:
        logger.exception("owner text action failed")
        await _reply(update, context, "❌ Internal error.")


async def _owner_delete_messages(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 args: list[str], text: str) -> None:
    """🧹 Messages deletions — platform owner only (the permanent archive is his to purge).
    all/shop need a typed YES; range takes two dates via the riders' report_window parser."""
    from app.riders.service import report_window

    if args[0] == "range":
        start, end, label = report_window(text.split())  # ValueError on junk → safe ⚠️ reply
        n = await delete_messages(start=start, end=end)
        await _reply(update, context, f"🗑 {n} message(s) deleted — {label}.")
        return
    if text.strip().upper() != "YES":
        await _reply(update, context, "Cancelled — nothing deleted.")
        return
    if args[0] == "shop":
        n = await delete_messages(shop_id=uuid.UUID(args[1]))
    else:
        n = await delete_messages()
    await _reply(update, context, f"🗑 {n} message(s) deleted.")


async def _owner_security_text(update: Update, context: ContextTypes.DEFAULT_TYPE,
                               do: str, text: str) -> None:
    """The security buttons' typed values → the same security-service calls the slash commands use."""
    from app.db.redis_client import get_redis
    from app.security import service as sec

    if not text:
        await _reply(update, context, "Nothing entered.")
        return
    if do == "oinv":
        inc = await sec.get_incident(text)
        await _reply(update, context, _format_incident(inc) if inc else "❌ No such incident.")
    elif do == "oqlift":
        await sec.lift_quarantine(get_redis(), text)
        await _reply(update, context, f"✅ Quarantine lifted for {text}.")
    elif do == "oqext":
        await sec.extend_quarantine(get_redis(), text)
        await _reply(update, context, f"⏳ Quarantine extended to 24h for {text}.")
    elif do == "oblk":
        parts = text.split(maxsplit=1)
        await sec.blacklist(get_redis(), parts[0], None, parts[1] if len(parts) > 1 else "owner blacklist")
        await _reply(update, context, f"⛔ {parts[0]} blacklisted. Their messages are now silently ignored.")
    elif do == "ofwd":
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await _reply(update, context, "Need <phone> <shop_id|number>.")
            return
        shop = await _resolve_shop(_service(context), parts[1])
        await sec.forward_to_shop(get_redis(), parts[0])
        await _reply(update, context, f"➡️ {parts[0]} now routed straight to {shop.name}'s staff.")
    elif do == "obyp":
        await sec.set_bypass(get_redis(), text)
        await _reply(update, context, f"➡️ {text} bypasses the AI — messages go straight to the shop's staff.")
    elif do == "obypr":
        await sec.remove_bypass(get_redis(), text)
        await _reply(update, context, f"✅ Bypass removed for {text} — the AI handles them again.")


def build_application(service: TenantService) -> Application:
    """Owner control bot: admin commands (/pauseshop, /shopstatus, ...)."""
    app = (
        ApplicationBuilder()
        .token(_owner_token())
        .build()
    )
    app.bot_data["tenant_service"] = service

    app.add_handler(MessageHandler(filters.ALL, _log_inbound), group=-1)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("menu", owner_menu_cmd))
    # owner (§2)
    app.add_handler(CommandHandler("pauseshop", pauseshop))
    app.add_handler(CommandHandler("resumeshop", resumeshop))
    app.add_handler(CommandHandler("shopstatus", shopstatus))
    # owner rider onboarding (§10)
    app.add_handler(CommandHandler("addrider", addrider))
    app.add_handler(CommandHandler("riders", owner_riders))
    # owner profit (§6)
    app.add_handler(CommandHandler("owner", owner_cmd))
    # owner security (§7, §8)
    for name, handler in OWNER_SECURITY_COMMANDS.items():
        app.add_handler(CommandHandler(name, handler))
    # shopkeeper stubs (§3/§5/§6/§10/§12)
    for name, (stage, _alias) in SHOPKEEPER_STUBS.items():
        app.add_handler(CommandHandler(name, _stub(name, stage)))
    app.add_handler(CallbackQueryHandler(_owner_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _owner_text))
    return app


# --- application factory: per-shop SHOPKEEPER bot (ADR-005) ---
async def _shopkeeper_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    shop: Shop = context.application.bot_data["shop"]
    await context.bot.send_message(update.effective_chat.id,
                                   f"🏪 {shop.name} — shopkeeper bot. Tap below or /help",
                                   reply_markup=kb.keeper_menu())


async def _shopkeeper_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    shop: Shop = context.application.bot_data["shop"]
    await _reply(
        update,
        context,
        f"🏪 {shop.name} — shopkeeper commands\n\n"
        "👉 /menu — do everything with buttons (no typing commands)\n\n"
        "Products:\n"
        "/addproduct — add a product (11 steps, /cancel any time)\n"
        "/boost <id> <1-10> · /unboost <id>\n"
        "/tag <id> <tag1,tag2> · /untag <id> <tag> · /cleartags <id>\n"
        "/feature <id>\n\n"
        "Escalated customers:\n"
        "/reply <customer> <text> — answer them yourself\n"
        "/handover <customer> — give them back to the assistant\n\n"
        "Orders:\n/orders — drafts awaiting your OK\n"
        "/confirmorder <#> · /rejectorder <#> [reason]\n"
        "/deliveryupdate <#> packed|shipped|delivered\n\n"
        "Delivery riders:\n/riders — list riders + ids\n"
        "/assigndelivery <order#> <rider_id>\n"
        "/reconcilecod <rider|name> <amount> — end-of-day cash\n\n"
        "Price requests:\n/approveprice <#> · /custom <#> <price> · /denyprice <#>\n"
        "/negotiation on|off\n\n"
        "Profit:\n/profit [today|yesterday|weekly|monthly|YYYY-MM-DD]\n\n"
        "Export:\n/exportorders [today|yesterday|YYYY-MM-DD|pending|all] [detailed]\n"
        "/exportrider <rider_id> [period]\n\n"
        "Stats:\n/productstats",
    )


async def _is_shopkeeper(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Is this Telegram user a registered shopkeeper of this bot's shop? Fail CLOSED on error."""
    shop = context.application.bot_data.get("shop")
    if shop is None:
        return False
    try:
        from app.db.factory import get_tenant_repo

        keepers = await get_tenant_repo().list_shopkeepers(shop.id)
        return any(sk.telegram_id == user_id for sk in keepers)
    except Exception:
        logger.exception("shopkeeper auth lookup failed shop=%s", shop.id)
        return False  # deny on error — never fail open on an auth check


async def _keeper_auth_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Runs first on the keeper bot. Blocks anyone who isn't a registered shopkeeper of this shop
    (the global owner is allowed). Telegram bots are publicly discoverable by @username, so without
    this ANY user could run /confirmorder, /approveprice (arbitrary discounts), /profit (revenue),
    or /exportorders (customer names/addresses/phones — PII). Covers every command AND the
    /addproduct conversation, because it intercepts the Update before any handler group."""
    user = update.effective_user
    if user and (is_owner(user.id) or await _is_shopkeeper(context, user.id)):
        return  # authorized → let the normal handlers run
    if update.message is not None:
        await _reply(update, context, "⛔ This bot is for shop staff only.")
    raise ApplicationHandlerStop  # stop ALL further handlers for this unauthorized update


async def keeper_menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/menu` — the shopkeeper's button menu."""
    await context.bot.send_message(update.effective_chat.id,
                                   f"🏪 {_shop_of(context).name}", reply_markup=kb.keeper_menu())


# Which slash prompt each product button maps to (button → guided '<id> <value>' reply).
_KPROD_PROMPT = {
    "kboost": "Reply with:  <PR0001> <1-10>", "kunboost": "Reply with:  <PR0001>",
    "ktag": "Reply with:  <PR0001> <tag1,tag2>", "kuntag": "Reply with:  <PR0001> <tag>",
    "kcleartags": "Reply with:  <PR0001>", "kfeature": "Reply with:  <PR0001>",
}


async def _keeper_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-button dispatcher for the keeper bot. Auth already enforced by the group -10 gate.
    Reuses the same service calls as the slash commands — a button is a second entry point only."""
    q = update.callback_query
    await q.answer()
    shop = _shop_of(context)
    action, args = kb.parse_cb(q.data)
    try:
        if action == "kmenu":
            await q.edit_message_text(f"🏪 {shop.name}", reply_markup=kb.keeper_menu())
        elif action == "korders":
            await orders_cmd(update, context)
        elif action == "kpr":
            await pricerequests_cmd(update, context)
        elif action == "kriders":
            await riders_cmd(update, context)
        elif action == "kstats":
            await q.edit_message_text("📊 Product stats for…", reply_markup=kb.keeper_stats_menu())
        elif action == "kstat":
            await _do_product_stats(update, context, args[0])
        elif action == "ksheet":
            await countersheet_cmd(update, context)
        elif action == "kids":
            await q.edit_message_text("🆔 Which IDs?", reply_markup=kb.keeper_ids_menu())
        elif action == "kidsp":
            from app.reports.service import format_id_list_products

            rows = await list_inventory(shop.id)
            await _reply(update, context, format_id_list_products(shop.name, rows))
        elif action == "kidsr":
            from app.reports.service import format_id_list_riders

            await _reply(update, context, format_id_list_riders(shop.name, await list_riders(shop.id)))
        elif action == "kprofmenu":
            await q.edit_message_text("📈 Profit for…", reply_markup=kb.keeper_profit_menu())
        elif action == "kexpmenu":
            await q.edit_message_text("📤 Export which orders?", reply_markup=kb.keeper_export_menu())
        elif action == "knegmenu":
            await q.edit_message_text("💬 Price negotiation:", reply_markup=kb.keeper_negotiation_menu())
        elif action == "kprodmenu":
            await q.edit_message_text("🏷 Product tools:", reply_markup=kb.keeper_product_menu())
        elif action == "krecmenu":
            riders = await list_riders(shop.id)
            if not riders:
                await _reply(update, context, "No riders yet. /addrider via the owner bot.")
            else:
                await q.edit_message_text("💵 Reconcile which rider?",
                                          reply_markup=kb.keeper_reconcile_picker(riders))
        elif action == "kconf":
            num = _order_number(args[0])
            await confirm_order(shop, num)
            await context.bot.send_message(update.effective_chat.id,
                                           f"✅ Order #{num} confirmed. Stock updated, customer notified.",
                                           reply_markup=kb.keeper_delivery_menu(num))
        elif action == "krej":
            context.chat_data["pending"] = {"do": "krej", "args": args}
            await _reply(update, context,
                         f"❌ Rejecting #{args[0]}. Reply with a reason, or send '-' for none.")
        elif action == "kdup":
            await advance_delivery(shop, _order_number(args[0]), args[1])
            await _reply(update, context, f"🚚 Order #{args[0]} → {args[1]}. Customer notified.")
        elif action == "kappr":
            price = await approve_price(shop, _order_number(args[0]))
            await _reply(update, context, f"✅ Approved at {price} AED. Customer notified.")
        elif action == "kcust":
            context.chat_data["pending"] = {"do": "kcust", "args": args}
            await _reply(update, context, f"✏️ Counter for request #{args[0]} — reply with your price (AED).")
        elif action == "kdeny":
            await deny_price(shop, _order_number(args[0]))
            await _reply(update, context, "❌ Declined. Customer told the list price stands.")
        elif action == "kasg":
            riders = await list_riders(shop.id)
            if not riders:
                await _reply(update, context, "No riders yet. /addrider via the owner bot.")
            else:
                await q.edit_message_text(f"🛵 Assign order #{args[0]} to…",
                                          reply_markup=kb.keeper_rider_picker(_order_number(args[0]), riders))
        elif action == "kasgr":
            res = await assign_delivery(shop, _order_number(args[0]), uuid.UUID(args[1]))
            tail = ("Rider notified. 📲" if res["notified"]
                    else "⚠️ Rider hasn't linked Telegram yet.")
            await _reply(update, context,
                         f"🛵 Order #{args[0]} → {res['rider']['name']} (COD {res['cod']} AED). {tail}")
        elif action == "krec":
            context.chat_data["pending"] = {"do": "krec", "args": args}
            await _reply(update, context, "💵 Reply with the cash amount the rider handed over (AED).")
        elif action == "kprof":
            start, end, label = parse_period(args[0])
            s = await profit_summary(shop.id, start, end)
            await _reply(update, context, format_profit(s, label))
        elif action in ("kexp", "kexpd"):
            name, url, n = await export_orders(shop, args[0], action == "kexpd")
            await _reply(update, context, f"📄 {n} order(s) — {name}\n{url}\n(link valid 24h)")
        elif action == "kneg":
            await set_negotiation(shop.id, args[0] == "on")
            await _reply(update, context,
                         "💬 Negotiation ON." if args[0] == "on" else "🔒 Negotiation OFF — no discounts.")
        elif action in _KPROD_PROMPT:
            context.chat_data["pending"] = {"do": action, "args": []}
            await _reply(update, context, _KPROD_PROMPT[action])
        else:
            await _reply(update, context, "Unknown action. /menu")
    except Exception as e:
        await _keeper_err(update, context, e)


async def _keeper_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Consume a free-text reply that a button asked for (reject reason, counter price, COD amount,
    product edit). No pending prompt → ignore (keeps the bot silent on stray text)."""
    pending = context.chat_data.get("pending")
    if not pending:
        return
    context.chat_data.pop("pending", None)
    shop = _shop_of(context)
    do, args = pending["do"], pending["args"]
    text = (update.message.text or "").strip()
    try:
        if do == "krej":
            reason = None if text in ("", "-") else text
            await reject_order(shop, _order_number(args[0]), reason)
            await _reply(update, context, f"❌ Order #{args[0]} rejected.")
        elif do == "kcust":
            price = await approve_price(shop, _order_number(args[0]), parse_price(text))
            await _reply(update, context, f"✅ Offered {price} AED to the customer.")
        elif do == "krec":
            from app.riders.service import parse_cash, reconcile_cod

            rider = await _resolve_rider(shop, args[0])
            trail = await reconcile_cod(shop, rider, parse_cash(text))
            tail = ("\n\n📲 Trail sent to the rider." if rider.get("telegram_id")
                    else "\n\n⚠️ Rider hasn't linked Telegram — trail not pushed.")
            await _reply(update, context, trail["text"] + tail)
        elif do in _KPROD_PROMPT:
            await _keeper_product_edit(update, context, shop, do, text)
    except Exception as e:
        await _keeper_err(update, context, e)


async def _keeper_product_edit(update, context, shop, do: str, text: str) -> None:
    """Apply a product button's '<product> <value>' reply via the existing product-service calls."""
    parts = text.split(maxsplit=1)
    if not parts:
        await _reply(update, context, "Nothing entered.")
        return
    pid = await _resolve_product(shop, parts[0])
    rest = parts[1] if len(parts) > 1 else ""
    if do == "kboost":
        p = await set_boost(shop.id, pid, parse_boost_level(rest))
        await _reply(update, context, f"🚀 {p.brand} {p.model} — boost {p.boost_level}/10.")
    elif do == "kunboost":
        p = await set_boost(shop.id, pid, 0)
        await _reply(update, context, f"✅ {p.brand} {p.model} — boost cleared.")
    elif do == "ktag":
        p = await add_tags(shop.id, pid, parse_tags(rest))
        await _reply(update, context, f"🏷 {p.brand} {p.model} — tags: {', '.join(p.tags) or '—'}")
    elif do == "kuntag":
        p = await remove_tag(shop.id, pid, rest)
        await _reply(update, context, f"🏷 {p.brand} {p.model} — tags: {', '.join(p.tags) or '—'}")
    elif do == "kcleartags":
        p = await clear_tags(shop.id, pid)
        await _reply(update, context, f"🏷 {p.brand} {p.model} — all tags removed.")
    elif do == "kfeature":
        p = await toggle_featured(shop.id, pid)
        await _reply(update, context, f"{'⭐' if p.is_featured else '☆'} {p.brand} {p.model} — featured: {p.is_featured}")


def build_shopkeeper_application(service: TenantService, shop: Shop) -> Application:
    """Per-shop shopkeeper bot: staff-side commands scoped to one shop."""
    if not shop.telegram_keeper_bot_token:
        raise RuntimeError(f"shop {shop.id} has no telegram_keeper_bot_token")
    app = ApplicationBuilder().token(shop.telegram_keeper_bot_token).build()
    app.bot_data["tenant_service"] = service
    app.bot_data["shop"] = shop
    app.add_handler(TypeHandler(Update, _keeper_auth_gate), group=-10)  # staff-only gate, runs first
    app.add_handler(MessageHandler(filters.ALL, _log_inbound), group=-1)
    app.add_handler(CommandHandler("start", _shopkeeper_start))
    app.add_handler(CommandHandler("help", _shopkeeper_help))
    app.add_handler(CommandHandler("menu", keeper_menu_cmd))
    # Stage 5: real product commands (SPEC §5) + the /addproduct flow (SPEC §4).
    app.add_handler(build_addproduct_handler())
    for name, handler in KEEPER_COMMANDS.items():
        app.add_handler(CommandHandler(name, handler))
    for name, (stage, _alias) in SHOPKEEPER_STUBS.items():
        if name not in KEEPER_REAL_COMMANDS:
            app.add_handler(CommandHandler(name, _stub(name, stage)))
    app.add_handler(CallbackQueryHandler(_keeper_cb))
    # Runs AFTER the /addproduct conversation, so an active add-flow keeps its text; otherwise a
    # pending button-prompt (reject reason, counter price, COD amount) claims the reply.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _keeper_text))
    return app


# --- application factory: per-shop CUSTOMER bot (ADR-005; = "WhatsApp" channel in testing) ---
async def _customer_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Customer inbound → SPEC §9 pipeline (Stage 3). WhatsApp mocked; this is the test channel (ADR-002)."""
    from app.db.redis_client import get_redis
    from app.messaging.pipeline import InboundMessage, process_message

    shop: Shop = context.application.bot_data["shop"]
    user = update.effective_user
    # Customer identity: Telegram user id in testing, phone in prod — same field (SPEC §1, ADR-005).
    identity = str(user.id) if user else str(update.effective_chat.id)
    msg = InboundMessage(shop=shop, identity=identity, text=update.message.text or "")
    result = await process_message(msg, get_redis())
    logger.info("customer msg shop=%s identity=%s action=%s media=%d",
                shop.id, identity, result.action, len(result.media))
    if result.reply is not None:
        await _reply(update, context, result.reply)
    await _send_media(update, context, result.media)


async def _send_media(update: Update, context: ContextTypes.DEFAULT_TYPE, media: tuple) -> None:
    """Send product photos/video the AI chose to show (SPEC §4 step 10). Best-effort — a failed
    media send must never break the text reply the customer already got."""
    chat_id = update.effective_chat.id
    for item in media:
        try:
            if item.get("type") == "video":
                await context.bot.send_video(chat_id, item["url"])
            else:
                await context.bot.send_photo(chat_id, item["url"])
        except Exception:
            logger.exception("send media failed chat=%s url=%s", chat_id, item.get("url"))


def build_customer_application(service: TenantService, shop: Shop) -> Application:
    """Per-shop customer bot: customer-facing channel (Telegram-first testing)."""
    if not shop.telegram_customer_bot_token:
        raise RuntimeError(f"shop {shop.id} has no telegram_customer_bot_token")
    app = ApplicationBuilder().token(shop.telegram_customer_bot_token).build()
    app.bot_data["tenant_service"] = service
    app.bot_data["shop"] = shop
    app.add_handler(MessageHandler(filters.ALL, _log_inbound), group=-1)
    app.add_handler(CommandHandler("start", _customer_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _customer_message))
    return app


async def _customer_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Customer bot greeting — no staff buttons here; the customer just chats (LLM handles it)."""
    shop: Shop = context.application.bot_data["shop"]
    await _reply(update, context, f"👋 Welcome to {shop.name}! How can I help you today?")


# --- application factory: global RIDER bot (SPEC §10 delivery) ---
_RIDER_HELP = (
    "🛵 Rider commands:\n"
    "👉 /menu — buttons for everything (no typing)\n"
    "/mydeliveries — your assignments + status\n"
    "/accept <order#> — confirm you HAVE the product\n"
    "/notreceived <order#> — product was NOT handed to you\n"
    "/deliver <order#> — mark delivered (then reply with cash received)\n"
    "/canceldelivery <order#> <remarks> — can't deliver (remarks required)\n"
    "/myreport [today|yesterday|weekly|monthly|YYYY-MM-DD [YYYY-MM-DD]] — your deliveries + cash"
)


async def _rider_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Rider bot /start — offer a Share-contact button so we can link their Telegram to their record."""
    from telegram import KeyboardButton, ReplyKeyboardMarkup

    from app.riders.service import riders_by_telegram

    user = update.effective_user
    if user and await riders_by_telegram(user.id):  # already linked → straight to work
        await context.bot.send_message(update.effective_chat.id, "🛵 Welcome back!",
                                       reply_markup=kb.rider_menu())
        return
    contact_kb = ReplyKeyboardMarkup(  # local name must not shadow the module alias `kb` (keyboards)
        [[KeyboardButton("📲 Share my phone number", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🛵 Rider bot.\nTap below to link your Telegram — then you'll get delivery assignments here.",
        reply_markup=contact_kb,
    )


async def _rider_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Rider shared their contact → match the phone to onboarded rider rows and store their chat id."""
    from app.riders.service import link_telegram

    contact = update.message.contact if update.message else None
    user = update.effective_user
    # Only accept the sender's OWN contact — a forwarded contact carries someone else's user_id.
    if contact is None or (contact.user_id and user and contact.user_id != user.id):
        await _reply(update, context, "Please tap the button and share YOUR own phone number.")
        return
    linked = await link_telegram(contact.phone_number, user.id)
    if not linked:
        await _reply(
            update, context,
            "❌ This number isn't registered as a rider yet. Ask the shop owner to add you first.",
        )
        return
    await context.bot.send_message(
        update.effective_chat.id,
        f"✅ Linked! You'll receive delivery assignments here ({len(linked)} shop(s)).",
        reply_markup=kb.rider_menu(),
    )


def rider_command(handler: Callable) -> Callable:
    """Wrap a rider command: auth by linked Telegram id, typed errors → safe replies."""

    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from app.riders.service import NotYourDelivery, riders_by_telegram

        user = update.effective_user
        rows = await riders_by_telegram(user.id) if user else []
        if not rows:
            await _reply(update, context,
                         "⛔ You're not linked as a rider. Press /start and share your phone number.")
            return
        try:
            await handler(update, context, rows)
        except NotYourDelivery as e:
            await _reply(update, context, f"❌ No delivery #{e} assigned to you. /mydeliveries to see yours.")
        except ValueError as e:
            await _reply(update, context, f"⚠️ {e}")
        except Exception:
            logger.exception("rider command failed")
            await _reply(update, context, "❌ Internal error.")
        else:
            await _audit(update, context, handler.__name__)  # §16: rider actions are logged too

    return wrapper


@rider_command
async def rider_help(update: Update, context: ContextTypes.DEFAULT_TYPE, rows: list[dict]) -> None:
    await _reply(update, context, _RIDER_HELP)


def _rider_status_icon(o: dict) -> str:
    if o["status"] == "delivered":
        return "✅ delivered"
    custody = o.get("custody") or "none"
    if custody == "disputed":
        return "🚨 disputed"
    if custody != "accepted":
        return f"⏳ pending — confirm pickup: /accept {o['order_number']}"
    return f"⏳ pending — /deliver {o['order_number']}"


@rider_command
async def rider_mydeliveries(update: Update, context: ContextTypes.DEFAULT_TYPE, rows: list[dict]) -> None:
    """`/mydeliveries` — every assignment with its status; pending ones show the next action."""
    from app.riders.service import my_deliveries

    orders = await my_deliveries([r["id"] for r in rows])
    if not orders:
        await _reply(update, context, "No deliveries assigned to you yet.")
        return
    await _reply(update, context, "🛵 Your deliveries:")
    for o in orders:
        p = o.get("products") or {}
        item = f"{p.get('brand', '')} {p.get('model', '')}".strip()
        cod = f" · COD {o['cod_amount']} AED" if o.get("cod_amount") is not None else ""
        text = (f"#{o['order_number']} — {item} ×{o['quantity']} — {o['address']}{cod}\n"
                f"   {_rider_status_icon(o)}")
        # Per-order message so its action buttons (accept/deliver/…) map to THIS order.
        markup = kb.rider_delivery_actions(o["order_number"], o.get("custody") or "none", o["status"])
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=markup)


@rider_command
async def rider_accept(update: Update, context: ContextTypes.DEFAULT_TYPE, rows: list[dict]) -> None:
    """`/accept <order#>` — 'yes, I have this product' (custody audit)."""
    from app.riders.service import set_custody

    parts = _split(update.message.text, maxsplit=1)
    if not parts:
        await _reply(update, context, "Usage: /accept <order_number>")
        return
    num = _order_number(parts[0])
    await set_custody([r["id"] for r in rows], rows[0]["name"], num, accept=True)
    await _reply(update, context,
                 f"✅ Pickup confirmed for order #{num}. Shop notified.\nWhen done: /deliver {num}")


@rider_command
async def rider_notreceived(update: Update, context: ContextTypes.DEFAULT_TYPE, rows: list[dict]) -> None:
    """`/notreceived <order#>` — 'this product was NOT handed to me' (custody audit)."""
    from app.riders.service import set_custody

    parts = _split(update.message.text, maxsplit=1)
    if not parts:
        await _reply(update, context, "Usage: /notreceived <order_number>")
        return
    num = _order_number(parts[0])
    await set_custody([r["id"] for r in rows], rows[0]["name"], num, accept=False)
    await _reply(update, context, f"🚨 Recorded: order #{num} not received. The shop has been alerted.")


@rider_command
async def rider_deliver(update: Update, context: ContextTypes.DEFAULT_TYPE, rows: list[dict]) -> None:
    """`/deliver <order#>` — register the delivery time, then ask for the cash received.
    The order finalizes (status/messages) when the rider replies with the amount."""
    parts = _split(update.message.text, maxsplit=1)
    if not parts:
        await _reply(update, context, "Usage: /deliver <order_number>")
        return
    await _do_rider_deliver(update, context, rows, _order_number(parts[0]))


async def _do_rider_deliver(update: Update, context: ContextTypes.DEFAULT_TYPE,
                            rows: list[dict], num: int) -> None:
    """Shared by /deliver and the 🚚 Deliver button: register time, then prompt for cash."""
    from datetime import datetime

    from app.reports.service import DUBAI
    from app.riders.service import _get_my_order, deliverable

    order = await _get_my_order([r["id"] for r in rows], num, None)  # raises NotYourDelivery
    reason = deliverable(order["status"], order.get("custody") or "none")
    if reason:
        raise ValueError(reason)
    now = datetime.now(DUBAI)
    # ponytail: pending cash lives in chat_data (in-memory). Bot restart mid-flow → rider just
    # re-runs /deliver. Finalize (status+messages) happens on the cash reply, delivery time = now.
    context.chat_data["await_cash"] = {"order": num, "at": now.isoformat()}
    cod = order.get("cod_amount")
    await _reply(
        update, context,
        f"🕐 Delivery time registered for order #{num} ({now:%H:%M})."
        + (f"\n💵 COD to collect: {cod} AED" if cod is not None else "")
        + "\n\nHow much cash did you receive? Reply with the amount (0 if none).",
    )


async def _rider_cash(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Plain-text reply on the rider bot = the cash amount for a pending /deliver."""
    from datetime import datetime

    from app.riders.service import NotYourDelivery, deliver_order, parse_cash, riders_by_telegram

    # A 🚫 Cancel button asks for remarks next — consume this text as those remarks.
    remarks_for = context.chat_data.get("await_remarks")
    if remarks_for is not None:
        context.chat_data.pop("await_remarks", None)
        text = (update.message.text or "").strip()
        if not text:
            await _reply(update, context, "Remarks are required. Cancel aborted — tap 🚫 again.")
            return
        user = update.effective_user
        rows = await riders_by_telegram(user.id) if user else []
        if not rows:
            return
        from app.riders.service import cancel_delivery
        try:
            await cancel_delivery([r["id"] for r in rows], rows[0]["name"], remarks_for, text)
        except Exception as e:
            await _reply(update, context, f"⚠️ {e}")
            return
        await _reply(update, context,
                     f"❌ Delivery #{remarks_for} cancelled. Shop and customer notified; stock restored.")
        return

    pending = context.chat_data.get("await_cash")
    if not pending:
        await _reply(update, context, _RIDER_HELP)
        return
    user = update.effective_user
    rows = await riders_by_telegram(user.id) if user else []
    if not rows:
        return
    try:
        cash = parse_cash(update.message.text or "")
        order = await deliver_order(
            [r["id"] for r in rows], rows[0]["name"], pending["order"],
            cash, datetime.fromisoformat(pending["at"]),
        )
    except ValueError as e:
        await _reply(update, context, f"⚠️ {e}")
        return
    except NotYourDelivery:
        context.chat_data.pop("await_cash", None)
        await _reply(update, context, "❌ That delivery is no longer yours.")
        return
    context.chat_data.pop("await_cash", None)
    cod = order.get("cod_amount")
    diff = ""
    if cod is not None and Decimal(str(cod)) != cash:
        diff = f"\n⚠️ Note: COD was {cod} AED, you entered {cash} AED — the shop sees both."
    await _reply(update, context,
                 f"✅ Order #{pending['order']} delivered. Cash recorded: {cash} AED. "
                 f"Customer and shop notified.{diff}")


@rider_command
async def rider_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE, rows: list[dict]) -> None:
    """`/canceldelivery <order#> <remarks>` — remarks are mandatory."""
    from app.riders.service import cancel_delivery

    parts = _split(update.message.text, maxsplit=2)
    if len(parts) < 2 or not parts[1].strip():
        await _reply(update, context,
                     "Usage: /canceldelivery <order_number> <remarks>\nRemarks are required.")
        return
    num = _order_number(parts[0])
    await cancel_delivery([r["id"] for r in rows], rows[0]["name"], num, parts[1].strip())
    await _reply(update, context,
                 f"❌ Delivery #{num} cancelled. Shop and customer notified; stock restored.")


@rider_command
async def rider_myreport(update: Update, context: ContextTypes.DEFAULT_TYPE, rows: list[dict]) -> None:
    """`/myreport [period]` or `/myreport <from> <to>` — deliveries done + cash collected."""
    await _do_rider_report(update, context, rows, _split(update.message.text, maxsplit=2))


def _format_rider_report(orders: list[dict], label: str, cash: Decimal) -> str:
    """Deliveries grouped by Dubai date, each with the detail a rider needs to recall the drop:
    order number, item, qty, ADDRESS, cash, time. Pure — the report's whole layout, testable."""
    from datetime import datetime

    from app.reports.service import DUBAI

    lines = [f"📊 Delivery report — {label}", "",
             f"Delivered: {len(orders)}", f"Cash collected: {cash} AED"]
    day = None
    for o in orders:
        stamp = o.get("delivered_at") or ""
        try:
            local = datetime.fromisoformat(stamp).astimezone(DUBAI)
        except ValueError:  # a malformed timestamp must not kill the report
            local = None
        d = f"{local:%b %d}" if local else "—"
        if d != day:  # date header, then that date's deliveries
            day, _ = d, lines.append("")
            lines.append(f"🗓 {d}")
        p = o.get("products") or {}
        item = f"{p.get('brand', '')} {p.get('model', '')}".strip() or "item"
        lines.append(f"  #{o['order_number']} — {item} ×{o.get('quantity', 1)}")
        if o.get("address"):
            lines.append(f"     📍 {o['address']}")
        t = f"{local:%H:%M}" if local else stamp[11:16]
        lines.append(f"     💵 {o.get('cash_received') or 0} AED · {t}")
    return "\n".join(lines)


async def _do_rider_report(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           rows: list[dict], args: list[str]) -> None:
    """Shared by /myreport and the report-period buttons."""
    from app.riders.service import cod_balance, delivered_report, report_window

    try:
        start, end, label = report_window(args)
    except ValueError:
        await _reply(update, context,
                     "Usage: /myreport [today|yesterday|weekly|monthly|YYYY-MM-DD] "
                     "or /myreport <YYYY-MM-DD> <YYYY-MM-DD>")
        return
    orders = await delivered_report([r["id"] for r in rows], start, end)
    cash = sum(Decimal(str(o["cash_received"] or 0)) for o in orders)
    lines = [_format_rider_report(orders, label, cash), ""]
    from app.db.factory import get_tenant_repo

    repo = get_tenant_repo()
    for r in rows:  # a rider may hold cash for more than one shop — show each
        shop = await repo.get_shop_by_id(uuid.UUID(r["shop_id"]))
        bal = await cod_balance(r["shop_id"], r["id"])
        lines.append(f"💰 Cash you hold — {shop.name if shop else 'shop'}: {bal} AED")
    await _reply(update, context, "\n".join(lines))


async def rider_menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/menu` — the rider's button menu (works only once they're linked)."""
    from app.riders.service import riders_by_telegram

    user = update.effective_user
    if not (user and await riders_by_telegram(user.id)):
        await _reply(update, context, "⛔ Press /start and share your phone number first.")
        return
    await context.bot.send_message(update.effective_chat.id, "🛵 What next?", reply_markup=kb.rider_menu())


async def _rider_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-button dispatcher for the rider bot. Reuses the same service calls as the commands."""
    from app.riders.service import NotYourDelivery, riders_by_telegram, set_custody

    q = update.callback_query
    await q.answer()  # stop Telegram's button spinner
    user = update.effective_user
    rows = await riders_by_telegram(user.id) if user else []
    if not rows:
        await _reply(update, context, "⛔ Press /start and share your phone number first.")
        return
    action, args = kb.parse_cb(q.data)
    rider_ids = [r["id"] for r in rows]
    try:
        if action == "rmenu":
            await q.edit_message_text("🛵 What next?", reply_markup=kb.rider_menu())
        elif action == "rmydel":
            await rider_mydeliveries(update, context)
        elif action == "rrepmenu":
            await q.edit_message_text("📊 Report for…", reply_markup=kb.rider_report_menu())
        elif action == "rrep":
            await _do_rider_report(update, context, rows, args)
        elif action == "racc":
            num = int(args[0])
            await set_custody(rider_ids, rows[0]["name"], num, accept=True)
            await _reply(update, context,
                         f"✅ Pickup confirmed for #{num}. Shop notified. When done, tap 🚚 Deliver.")
        elif action == "rnrx":
            num = int(args[0])
            await set_custody(rider_ids, rows[0]["name"], num, accept=False)
            await _reply(update, context, f"🚨 Recorded: #{num} not received. The shop has been alerted.")
        elif action == "rdel":
            await _do_rider_deliver(update, context, rows, int(args[0]))
        elif action == "rcan":
            context.chat_data["await_remarks"] = int(args[0])
            await _reply(update, context,
                         f"🚫 Cancelling #{args[0]}. Reply with the reason (remarks are required).")
        else:
            await _reply(update, context, "Unknown action. /menu")
    except NotYourDelivery as e:
        await _reply(update, context, f"❌ No delivery #{e} assigned to you.")
    except ValueError as e:
        await _reply(update, context, f"⚠️ {e}")


def build_rider_application(service: TenantService) -> Application:
    """Global rider bot: link Telegram, receive assignments, work them (SPEC §10)."""
    from app.core.config import settings

    if not settings.telegram_rider_bot_token:
        raise RuntimeError("TELEGRAM_RIDER_BOT_TOKEN not set (see .env.example).")
    app = ApplicationBuilder().token(settings.telegram_rider_bot_token).build()
    app.bot_data["tenant_service"] = service
    app.add_handler(MessageHandler(filters.ALL, _log_inbound), group=-1)
    app.add_handler(CommandHandler("start", _rider_start))
    app.add_handler(CommandHandler("help", rider_help))
    app.add_handler(CommandHandler("menu", rider_menu_cmd))
    app.add_handler(CommandHandler("mydeliveries", rider_mydeliveries))
    app.add_handler(CommandHandler("accept", rider_accept))
    app.add_handler(CommandHandler("notreceived", rider_notreceived))
    app.add_handler(CommandHandler("deliver", rider_deliver))
    app.add_handler(CommandHandler("canceldelivery", rider_cancel))
    app.add_handler(CommandHandler("myreport", rider_myreport))
    app.add_handler(CallbackQueryHandler(_rider_cb))
    app.add_handler(MessageHandler(filters.CONTACT, _rider_contact))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _rider_cash))
    return app


# --- application factory: global SHOP-OWNER bot (ADR-006 clients) ---
# The client who owns 1+ shops watches them remotely: profit, orders, inventory, riders' COD,
# exports, and customer messages — so nobody in the shop can hide cancellations or discounts.
# Deliberately NO security/escalation views: those are platform-owner business (owner bot).
_SHOPOWNER_HELP = (
    "🏢 Shop-owner bot — remote oversight of YOUR shops.\n"
    "/menu — buttons for everything:\n"
    "• My shops → per-shop profit, orders, inventory, riders' COD, Excel export, messages\n"
    "• Analytics → compare shops, top products, cancellations & discounts, COD outstanding\n"
    "• Messages → read your shops' customer conversations"
)

_SHOPOWNER_DENY = "⛔ You're not linked as a shop owner. Press /start and share your phone number."


async def _shopowner_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shop-owner bot /start — offer a Share-contact button to link their Telegram to their client row."""
    from telegram import KeyboardButton, ReplyKeyboardMarkup

    user = update.effective_user
    client = await _service(context).client_by_telegram(user.id) if user else None
    if client:  # already linked → straight to work
        await context.bot.send_message(update.effective_chat.id, f"🏢 Welcome back, {client.name}!",
                                       reply_markup=kb.shopowner_menu())
        return
    contact_kb = ReplyKeyboardMarkup(  # local name must not shadow the module alias `kb` (keyboards)
        [[KeyboardButton("📲 Share my phone number", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="🏢 Shop-owner bot.\nTap below to link your Telegram — then you can watch your shops from here.",
        reply_markup=contact_kb,
    )


async def _shopowner_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner shared their contact → match the phone to their client row and store their chat id."""
    contact = update.message.contact if update.message else None
    user = update.effective_user
    # Only accept the sender's OWN contact — a forwarded contact carries someone else's user_id.
    if contact is None or (contact.user_id and user and contact.user_id != user.id):
        await _reply(update, context, "Please tap the button and share YOUR own phone number.")
        return
    linked = await _service(context).link_client_telegram(contact.phone_number, user.id)
    if not linked:
        await _reply(update, context,
                     "❌ This number isn't registered as a client. Ask the platform owner to add you first.")
        return
    await context.bot.send_message(update.effective_chat.id,
                                   f"✅ Linked! Welcome, {linked[0].name}. Your shops are below.",
                                   reply_markup=kb.shopowner_menu())


def shopowner_command(handler: Callable) -> Callable:
    """Wrap a shop-owner command: auth by linked client Telegram id, typed errors → safe replies."""

    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        client = await _service(context).client_by_telegram(user.id) if user else None
        if client is None:
            await _reply(update, context, _SHOPOWNER_DENY)
            return
        try:
            await handler(update, context, client)
        except (ShopNotFound, ClientNotFound):
            await _reply(update, context, "❌ Not found.")
        except ValueError as e:
            await _reply(update, context, f"⚠️ {e}")
        except Exception:
            logger.exception("shop-owner command failed")
            await _reply(update, context, "❌ Internal error.")
        else:
            await _audit(update, context, handler.__name__)  # §16: owner reads are logged too

    return wrapper


@shopowner_command
async def shopowner_help(update: Update, context: ContextTypes.DEFAULT_TYPE, client: Client) -> None:
    await _reply(update, context, _SHOPOWNER_HELP)


@shopowner_command
async def shopowner_menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, client: Client) -> None:
    await context.bot.send_message(update.effective_chat.id, f"🏢 {client.name}",
                                   reply_markup=kb.shopowner_menu())


async def _own_shop(service: TenantService, client: Client, sid: str) -> Shop:
    """THE tenant guard: resolve a callback's shop id WITHIN this client. Unknown and foreign
    shops fail identically — never confirm another client's shop exists."""
    try:
        shop = await service.get_shop(uuid.UUID(sid))
    except ValueError:
        raise ShopNotFound(sid) from None
    if shop.client_id != client.id:
        raise ShopNotFound(sid)
    return shop


def _format_owner_orders(shop: Shop, rows: list[dict], filt: str) -> str:
    """One line per order for the 📦 Orders button; cancelled rows show their remarks."""
    if not rows:
        return f"📦 {shop.name} — no orders ({filt})."
    lines = [f"📦 {shop.name} — {len(rows)} order(s) ({filt}):", ""]
    for o in rows[:30]:
        p = o.get("products") or {}
        item = f"{p.get('brand', '')} {p.get('model', '')}".strip()
        net = float(o.get("selling_price") or 0) - float(o.get("discount_amount") or 0)
        line = (f"#{o['order_number']} · {o.get('customer_name', '?')} · {item}"
                f" ×{o.get('quantity', 1)} · {net:,.0f} AED · {o.get('status', '?')}")
        if o.get("status") == "cancelled" and o.get("cancel_remarks"):
            line += f"\n   ❌ {o['cancel_remarks']}"
        lines.append(line)
    if len(rows) > 30:
        lines.append(f"… {len(rows) - 30} more — use 📤 Export Excel")
    return "\n".join(lines)


async def _shopowner_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-button dispatcher for the shop-owner bot. Every shop-id-carrying action resolves
    the shop through `_own_shop` first — a crafted foreign shop_id gets '❌ Not found'."""
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    service = _service(context)
    client = await service.client_by_telegram(user.id) if user else None
    if client is None:
        await _reply(update, context, _SHOPOWNER_DENY)
        return
    action, args = kb.parse_cb(q.data)
    try:
        if action == "smenu":
            await q.edit_message_text(f"🏢 {client.name}", reply_markup=kb.shopowner_menu())
        elif action in ("sshops", "smsgs"):
            shops = await service.list_shops_by_client(client.id)
            if not shops:
                await _reply(update, context, "No shops yet.")
            else:
                picker = [{"id": str(s.id), "name": s.name} for s in shops]
                pick = "smsg" if action == "smsgs" else "sshop"
                await q.edit_message_text("🏪 Which shop?",
                                          reply_markup=kb.shopowner_shop_picker(picker, pick))
        elif action == "sshop":
            shop = await _own_shop(service, client, args[0])
            await q.edit_message_text(f"🏪 {shop.name}:",
                                      reply_markup=kb.shopowner_shop_actions(args[0]))
        elif action == "sprofmenu":
            shop = await _own_shop(service, client, args[0])
            await q.edit_message_text(f"📈 {shop.name} — profit for…",
                                      reply_markup=kb.shopowner_shop_period_menu(
                                          "sprof", args[0], kb.cb("sshop", args[0])))
        elif action == "sprof":
            shop = await _own_shop(service, client, args[0])
            start, end, label = parse_period(args[1])
            s = await profit_summary(shop.id, start, end)
            await _reply(update, context, format_profit(s, f"{shop.name} — {label}"))
        elif action == "sordmenu":
            shop = await _own_shop(service, client, args[0])
            await q.edit_message_text(f"📦 {shop.name} — orders:",
                                      reply_markup=kb.shopowner_orders_menu(args[0]))
        elif action == "sord":
            shop = await _own_shop(service, client, args[0])
            rows = await orders_for_export(shop.id, args[1])
            await _reply(update, context, _format_owner_orders(shop, rows, args[1]))
        elif action == "sinv":
            shop = await _own_shop(service, client, args[0])
            await _reply(update, context, format_inventory(shop.name, await list_inventory(shop.id)))
        elif action == "scod":
            shop = await _own_shop(service, client, args[0])
            riders = await list_riders(shop.id)
            pairs = [(r["name"], await cod_balance(shop.id, r["id"])) for r in riders]
            await _reply(update, context, format_cod_outstanding([(shop.name, pairs)]))
        elif action == "sexpmenu":
            shop = await _own_shop(service, client, args[0])
            await q.edit_message_text(f"📤 {shop.name} — export:",
                                      reply_markup=kb.shopowner_export_menu(args[0]))
        elif action in ("sexp", "sexpd"):
            shop = await _own_shop(service, client, args[0])
            name, url, count = await export_orders(shop, args[1], detailed=(action == "sexpd"))
            await _reply(update, context, f"📄 {count} order(s) — {name}\n{url}\n(link valid 24h)")
        elif action == "smsg":
            shop = await _own_shop(service, client, args[0])
            convs = await conversations(shop.id)
            await context.bot.send_message(update.effective_chat.id,
                                           format_conversations(shop.name, convs),
                                           reply_markup=kb.shopowner_conversations_kb(args[0], convs))
        elif action == "smsgc":
            shop = await _own_shop(service, client, args[0])
            identity = ":".join(args[1:])  # identities are phones/tg ids; join defensively
            await _reply(update, context, format_transcript(identity, await transcript(shop.id, identity)))
        elif action == "sanmenu":
            await q.edit_message_text("📊 Analytics — all your shops:",
                                      reply_markup=kb.shopowner_analytics_menu())
        elif action in ("scmpmenu", "stopmenu", "scanmenu"):
            titles = {"scmpmenu": "↔️ Compare shops for…", "stopmenu": "🏆 Top products for…",
                      "scanmenu": "🕵️ Cancels & discounts for…"}
            await q.edit_message_text(titles[action],
                                      reply_markup=kb.shopowner_period_menu(action[:-4], kb.cb("sanmenu")))
        elif action in ("scmp", "stop"):
            start, end, label = parse_period(args[0])
            shops = await service.list_shops_by_client(client.id)
            items = [(s.name, await profit_summary(s.id, start, end)) for s in shops]
            text = (format_owner_profit(items, label) if action == "scmp"
                    else format_top_products(items, label))
            await _reply(update, context, text)
        elif action == "scan":
            start, end, label = parse_period(args[0])
            shops = await service.list_shops_by_client(client.id)
            per_shop = [(s.name,
                         await cancelled_orders(s.id, start, end),
                         await discounted_orders(s.id, start, end)) for s in shops]
            await _reply(update, context, format_audit_report(per_shop, label))
        elif action == "scodall":
            shops = await service.list_shops_by_client(client.id)
            per_shop = []
            for s in shops:
                riders = await list_riders(s.id)
                per_shop.append((s.name, [(r["name"], await cod_balance(s.id, r["id"])) for r in riders]))
            await _reply(update, context, format_cod_outstanding(per_shop))
        else:
            await _reply(update, context, "Unknown action. /menu")
    except (ShopNotFound, ClientNotFound):
        await _reply(update, context, "❌ Not found.")
    except ValueError as e:
        await _reply(update, context, f"⚠️ {e}")
    except Exception:
        logger.exception("shop-owner button failed")
        await _reply(update, context, "❌ Internal error.")


def build_shopowner_application(service: TenantService) -> Application:
    """Global shop-owner bot: client owners watch their shops remotely (ADR-006).
    All-button UX — no free-text handler; every input is a tap."""
    from app.core.config import settings

    if not settings.telegram_shopowner_bot_token:
        raise RuntimeError("TELEGRAM_SHOPOWNER_BOT_TOKEN not set (see .env.example).")
    app = ApplicationBuilder().token(settings.telegram_shopowner_bot_token).build()
    app.bot_data["tenant_service"] = service
    app.add_handler(MessageHandler(filters.ALL, _log_inbound), group=-1)
    app.add_handler(CommandHandler("start", _shopowner_start))
    app.add_handler(CommandHandler("help", shopowner_help))
    app.add_handler(CommandHandler("menu", shopowner_menu_cmd))
    app.add_handler(CallbackQueryHandler(_shopowner_cb))
    app.add_handler(MessageHandler(filters.CONTACT, _shopowner_contact))
    return app


def _owner_token() -> str:
    from app.core.config import settings

    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set (see .env.example).")
    return settings.telegram_bot_token


def run_polling(service: TenantService) -> None:
    """Long-polling runner for the OWNER bot only (ADR-002). For quick smoke tests."""
    app = build_application(service)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


# --- multi-bot runner: owner + every shop's keeper + customer bots (ADR-005) ---
async def _build_all_applications(service: TenantService) -> list[Application]:
    """Owner app + one shopkeeper app + one customer app per shop + a global rider app."""
    from app.core.config import settings

    apps = [build_application(service)]
    shops = await service.list_shops()
    for shop in shops:
        if shop.telegram_keeper_bot_token:
            apps.append(build_shopkeeper_application(service, shop))
        if shop.telegram_customer_bot_token:
            apps.append(build_customer_application(service, shop))
    if settings.telegram_rider_bot_token:  # global rider bot (delivery assignments)
        apps.append(build_rider_application(service))
    if settings.telegram_shopowner_bot_token:  # global shop-owner bot (client reports, ADR-006)
        apps.append(build_shopowner_application(service))
    return apps


async def _run_apps_forever(service: TenantService) -> None:
    apps = await _build_all_applications(service)
    if len(apps) == 1:
        logger.warning("no per-shop bots configured — running owner bot only")
    for app in apps:
        await app.initialize()
        if app.updater is not None:
            await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        await app.start()
    logger.info("polling started: %d bot(s)", len(apps))

    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            # Windows ProactorEventLoop has no add_signal_handler — fall back to KeyboardInterrupt.
            pass

    try:
        await stop.wait()
    finally:
        for app in reversed(apps):
            try:
                await app.stop()
            except Exception:
                logger.exception("stop failed for bot")
            if app.updater is not None:
                try:
                    await app.updater.stop()
                except Exception:
                    logger.exception("updater.stop failed for bot")
            try:
                await app.shutdown()
            except Exception:
                logger.exception("shutdown failed for bot")


def run_all_polling(service: TenantService) -> None:
    """Run owner + all shop bots concurrently under one event loop (ADR-005)."""
    from app.core.logging import setup_logging

    setup_logging()  # §16: structured logs for the bot process
    try:
        asyncio.run(_run_apps_forever(service))
    except KeyboardInterrupt:
        pass
