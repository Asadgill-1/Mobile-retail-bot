"""Delivery riders (SPEC §10): onboarding, Telegram linking, custody, delivery + COD ledger.

Owner onboards riders (name + phone); a rider links their Telegram on the global rider bot by
sharing their contact. From there the rider works their assignments:

  custody:  shop assigns → 'offered' → rider /accept ('accepted' = "I have the product") or
            /notreceived ('disputed'). Neither side can later dispute the handover — it's logged.
  deliver:  rider /deliver (only an *accepted* order) → delivery time + cash registered, customer
            and shop notified, a 'collect' row appended to cod_ledger.
  cancel:   rider /canceldelivery with mandatory remarks → order cancelled, stock restored.
  money:    cod_ledger is append-only ('collect' / 'handover'); balance = Σcollect − Σhandover.
            Keeper /reconcilecod writes the 'handover' row and sends both sides the trail.

Direct Supabase calls wrapped in asyncio.to_thread, tenant-scoped like orders/service.py.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

# Shared writers/rules from orders — one place writes status history, one RPC moves stock.
from app.orders.service import _decrement_stock, _set_status
from app.reports.service import DUBAI, parse_period

logger = logging.getLogger(__name__)

# Statuses a rider can act on: assigned and still in flight.
_ACTIVE = ("confirmed", "packed", "shipped")
_ORDER_SELECT = "*, products(brand,model,color)"


class RiderNotFound(Exception):
    """No rider with this id in this shop (unknown, or another shop's)."""


class NotYourDelivery(Exception):
    """Order number that isn't assigned to this rider (unknown, or someone else's)."""


def _sb(client: Any | None) -> Any:
    from app.db.supabase_client import get_supabase

    return client if client is not None else get_supabase()


def _normalize_phone(raw: str) -> str:
    """UAE mobile → the 9 significant digits, so an owner-typed number links to a Telegram contact.

    '0501234567', '+971501234567', '971 50 123 4567' all → '501234567'. Telegram hands us the
    contact with a country code; the owner may have typed a local 0-prefixed number — this makes
    both sides comparable. (UAE-only, per the business.)
    """
    digits = "".join(c for c in (raw or "") if c.isdigit())
    if digits.startswith("971"):
        digits = digits[3:]
    return digits.lstrip("0")


# ---------------------------------------------------------------------------
# Onboarding + linking (owner side / rider bot /start)
# ---------------------------------------------------------------------------
async def add_rider(shop_id: UUID, name: str, phone: str, client: Any | None = None) -> dict:
    """Owner onboards a rider for a shop. Returns the inserted row (incl. id)."""
    sb = _sb(client)

    def _q() -> dict:
        return sb.table("delivery_persons").insert(
            {"shop_id": str(shop_id), "name": name, "phone": phone}
        ).execute().data[0]

    return await asyncio.to_thread(_q)


async def list_riders(shop_id: UUID, client: Any | None = None) -> list[dict]:
    """All riders of a shop, for `/riders`."""
    sb = _sb(client)

    def _q() -> list[dict]:
        return (
            sb.table("delivery_persons").select("*")
            .eq("shop_id", str(shop_id)).order("created_at").execute().data or []
        )

    return await asyncio.to_thread(_q)


async def get_rider(shop_id: UUID, rider_id: UUID, client: Any | None = None) -> dict:
    """One rider within this shop (tenant guard). Raises RiderNotFound for unknown/another shop's."""
    sb = _sb(client)

    def _q() -> dict | None:
        rows = (
            sb.table("delivery_persons").select("*")
            .eq("shop_id", str(shop_id)).eq("id", str(rider_id)).limit(1).execute().data or []
        )
        return rows[0] if rows else None

    rider = await asyncio.to_thread(_q)
    if rider is None:
        raise RiderNotFound(rider_id)
    return rider


async def link_telegram(phone: str, telegram_id: int, client: Any | None = None) -> list[dict]:
    """Rider bot /start → contact shared. Store their chat id on every rider row that matches the
    phone (a person may ride for more than one shop). Returns the linked rows (empty = no match)."""
    sb = _sb(client)
    target = _normalize_phone(phone)
    if not target:
        return []

    def _q() -> list[dict]:
        rows = sb.table("delivery_persons").select("*").execute().data or []
        matched = [r for r in rows if _normalize_phone(r.get("phone", "")) == target]
        for r in matched:
            sb.table("delivery_persons").update({"telegram_id": telegram_id}).eq("id", r["id"]).execute()
            r["telegram_id"] = telegram_id
        return matched

    return await asyncio.to_thread(_q)


async def riders_by_telegram(telegram_id: int, client: Any | None = None) -> list[dict]:
    """Every rider row linked to this Telegram account — the rider-bot auth lookup."""
    sb = _sb(client)

    def _q() -> list[dict]:
        return (
            sb.table("delivery_persons").select("*")
            .eq("telegram_id", telegram_id).execute().data or []
        )

    return await asyncio.to_thread(_q)


# ---------------------------------------------------------------------------
# Pure rules (unit-tested): custody transitions, deliverability, cash, windows, COD math.
# ---------------------------------------------------------------------------
def custody_transition(current: str, accept: bool) -> str:
    """/accept → 'accepted', /notreceived → 'disputed'. Only from a pending handover state.

    'none' is allowed as a source (orders assigned before this feature existed); a decided state
    ('accepted'/'disputed') can't be re-decided — the audit answer is written once.
    """
    if current in ("accepted", "disputed"):
        raise ValueError(f"pickup already answered: '{current}' — it can't be changed")
    return "accepted" if accept else "disputed"


def deliverable(status: str, custody: str) -> str | None:
    """None if the rider may /deliver now, else the human reason they can't."""
    if status == "delivered":
        return "already delivered"
    if status not in _ACTIVE:
        return f"order is '{status}' — nothing to deliver"
    if custody != "accepted":
        return "confirm pickup first: /accept <order#> (or /notreceived if you don't have it)"
    return None


def parse_cash(text: str) -> Decimal:
    """Rider's cash reply → Decimal AED. '3,400' ok. Rejects negative/junk."""
    try:
        amount = Decimal((text or "").strip().replace(",", "").replace("AED", "").strip())
    except InvalidOperation:
        raise ValueError(f"'{text}' is not an amount — reply with a number, e.g. 3400 (0 if prepaid)")
    if amount < 0:
        raise ValueError("cash received can't be negative")
    return amount


def _day(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=DUBAI)


def report_window(args: list[str], today: date | None = None) -> tuple[datetime, datetime, str]:
    """/myreport windows: none → today; one arg → today|yesterday|weekly|monthly|YYYY-MM-DD;
    two args → from-date to-date (inclusive)."""
    if len(args) >= 2:
        a, b = date.fromisoformat(args[0]), date.fromisoformat(args[1])
        if b < a:
            a, b = b, a
        return _day(a), _day(b + timedelta(days=1)), f"{a:%b %d} – {b:%b %d, %Y}"
    return parse_period(args[0] if args else "today", today)


def cod_trail(rows: list[dict], today_start: datetime) -> dict:
    """Fold the ledger into the reconcile numbers: previous balance (before today), today's
    collections/handovers, and the running balance. Pure over ledger rows."""
    prev_c = prev_h = today_c = today_h = Decimal("0")
    for r in rows:
        amount = Decimal(str(r["amount"]))
        ts = datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00"))
        is_today = ts >= today_start
        if r["entry"] == "collect":
            today_c += amount if is_today else 0
            prev_c += 0 if is_today else amount
        else:
            today_h += amount if is_today else 0
            prev_h += 0 if is_today else amount
    prev = prev_c - prev_h
    return {
        "previous": prev,
        "today_collect": today_c,
        "today_handover": today_h,
        "balance": prev + today_c - today_h,
    }


# ---------------------------------------------------------------------------
# Rider actions (rider bot)
# ---------------------------------------------------------------------------
async def _get_my_order(rider_ids: list[str], order_number: int, client: Any | None) -> dict:
    """The order by number IF it's assigned to one of this rider's rows (auth by assignment)."""
    sb = _sb(client)

    def _q() -> dict | None:
        rows = (
            sb.table("orders").select(_ORDER_SELECT)
            .eq("order_number", order_number).in_("rider_id", rider_ids)
            .limit(1).execute().data or []
        )
        return rows[0] if rows else None

    order = await asyncio.to_thread(_q)
    if order is None:
        raise NotYourDelivery(order_number)  # unknown or someone else's — same reply either way
    return order


async def my_deliveries(rider_ids: list[str], client: Any | None = None) -> list[dict]:
    """The rider's assignments: everything in flight + recent delivered (newest first)."""
    sb = _sb(client)

    def _q() -> list[dict]:
        return (
            sb.table("orders").select(_ORDER_SELECT)
            .in_("rider_id", rider_ids).in_("status", list(_ACTIVE) + ["delivered"])
            .order("created_at", desc=True).limit(15).execute().data or []
        )

    return await asyncio.to_thread(_q)


async def _shop_of_order(order: dict):
    from app.db.factory import get_tenant_repo

    shop = await get_tenant_repo().get_shop_by_id(UUID(order["shop_id"]))
    if shop is None:  # can't happen (FK), but never act on a shopless order
        raise RiderNotFound(order["shop_id"])
    return shop


async def _notify_shop(shop, text: str) -> None:
    """Best-effort staff notification (mirrors orders._notify_shop)."""
    from app.db.factory import get_tenant_repo
    from app.telegram_bot.notify import send_to_shopkeepers

    try:
        keepers = await get_tenant_repo().list_shopkeepers(shop.id)
        await send_to_shopkeepers(shop, keepers, text)
    except Exception:
        logger.exception("rider notify failed shop=%s", shop.id)


async def set_custody(
    rider_ids: list[str], rider_name: str, order_number: int, accept: bool,
    client: Any | None = None,
) -> dict:
    """/accept | /notreceived — write the pickup-handover answer and tell the shop.

    This is the audit handshake: once 'accepted', the rider can't claim "never got the product";
    while 'disputed', the shop is alerted immediately and the order can't be delivered.
    """
    order = await _get_my_order(rider_ids, order_number, client)
    new = custody_transition(order["custody"], accept)  # raises ValueError if already answered
    sb = _sb(client)

    def _q() -> None:
        sb.table("orders").update(
            {"custody": new, "custody_at": datetime.now(DUBAI).isoformat()}
        ).eq("id", order["id"]).execute()

    await asyncio.to_thread(_q)
    shop = await _shop_of_order(order)
    if accept:
        await _notify_shop(shop, f"✅ Pickup confirmed — {rider_name} has order #{order_number} in hand.")
    else:
        await _notify_shop(
            shop,
            f"🚨 PICKUP DISPUTED — {rider_name} says order #{order_number} was NOT handed to them. "
            f"Check the package with your staff.",
        )
    order["custody"] = new
    return order


async def deliver_order(
    rider_ids: list[str], rider_name: str, order_number: int,
    cash: Decimal, delivered_at: datetime, client: Any | None = None,
) -> dict:
    """Finalize a delivery: status → delivered, time + cash on the order, 'collect' ledger row,
    customer + shop notified. Only an order this rider accepted custody of."""
    order = await _get_my_order(rider_ids, order_number, client)
    reason = deliverable(order["status"], order.get("custody") or "none")
    if reason:
        raise ValueError(reason)

    sb = _sb(client)
    await _set_status(order["id"], "delivered", "rider", client)  # shared history writer

    def _q() -> None:
        sb.table("orders").update(
            {"delivered_at": delivered_at.isoformat(), "cash_received": str(cash)}
        ).eq("id", order["id"]).execute()
        sb.table("cod_ledger").insert(
            {
                "shop_id": order["shop_id"], "rider_id": order["rider_id"], "order_id": order["id"],
                "entry": "collect", "amount": str(cash), "note": f"order #{order_number} delivered",
            }
        ).execute()

    await asyncio.to_thread(_q)

    shop = await _shop_of_order(order)
    from app.orders.service import _remember_to_customer
    from app.telegram_bot.notify import send_to_customer

    msg = f"✅ Order #{order_number} has been delivered. Thank you for shopping with {shop.name}! 🙏"
    await send_to_customer(shop, order["phone"], msg)
    await _remember_to_customer(shop, order["phone"], msg)
    await _notify_shop(
        shop,
        f"📬 Order #{order_number} delivered by {rider_name} at {delivered_at:%H:%M}.\n"
        f"Cash received: {cash} AED"
        + (f" (COD was {order['cod_amount']} AED)" if order.get("cod_amount") is not None else ""),
    )
    return order


async def cancel_delivery(
    rider_ids: list[str], rider_name: str, order_number: int, remarks: str,
    client: Any | None = None,
) -> dict:
    """/canceldelivery — rider can't complete the delivery. Remarks are mandatory (the caller
    enforces non-empty). Stock goes back, shop is told why, customer gets a soft message."""
    order = await _get_my_order(rider_ids, order_number, client)
    if order["status"] not in _ACTIVE:
        raise ValueError(f"order is '{order['status']}' — only an in-flight delivery can be cancelled")

    await _set_status(order["id"], "cancelled", "rider", client)
    sb = _sb(client)

    def _q() -> None:
        sb.table("orders").update({"cancel_remarks": remarks}).eq("id", order["id"]).execute()

    await asyncio.to_thread(_q)
    # Stock was decremented at confirm; a cancelled delivery puts it back. The decrement RPC with
    # a NEGATIVE n increments (quantity >= -n is always true for qty >= 0) — reusing the one
    # atomic stock writer instead of a second RPC.
    await _decrement_stock(UUID(order["shop_id"]), order["product_id"], -int(order["quantity"]), client)

    shop = await _shop_of_order(order)
    from app.orders.service import _remember_to_customer
    from app.telegram_bot.notify import send_to_customer

    await _notify_shop(
        shop,
        f"❌ Delivery cancelled — order #{order_number} by {rider_name}.\n"
        f"Remarks: {remarks}\nStock restored (+{order['quantity']}).",
    )
    msg = (f"We're sorry — delivery of order #{order_number} couldn't be completed. "
           f"{shop.name} will contact you shortly.")
    await send_to_customer(shop, order["phone"], msg)
    await _remember_to_customer(shop, order["phone"], msg)
    return order


async def delivered_report(
    rider_ids: list[str], start: datetime, end: datetime, client: Any | None = None
) -> list[dict]:
    """Deliveries this rider completed in [start, end) — by delivery time, for /myreport."""
    sb = _sb(client)

    def _q() -> list[dict]:
        return (
            sb.table("orders").select(_ORDER_SELECT)
            .in_("rider_id", rider_ids).eq("status", "delivered")
            .gte("delivered_at", start.isoformat()).lt("delivered_at", end.isoformat())
            .order("delivered_at").execute().data or []
        )

    return await asyncio.to_thread(_q)


# ---------------------------------------------------------------------------
# COD ledger (money audit)
# ---------------------------------------------------------------------------
async def cod_rows(shop_id: str | UUID, rider_id: str | UUID, client: Any | None = None) -> list[dict]:
    sb = _sb(client)

    def _q() -> list[dict]:
        return (
            sb.table("cod_ledger").select("entry,amount,created_at")
            .eq("shop_id", str(shop_id)).eq("rider_id", str(rider_id))
            .order("created_at").execute().data or []
        )

    return await asyncio.to_thread(_q)


async def cod_balance(shop_id: str | UUID, rider_id: str | UUID, client: Any | None = None) -> Decimal:
    """Cash the rider currently holds for this shop: Σcollect − Σhandover."""
    rows = await cod_rows(shop_id, rider_id, client)
    total = Decimal("0")
    for r in rows:
        amount = Decimal(str(r["amount"]))
        total += amount if r["entry"] == "collect" else -amount
    return total


async def reconcile_cod(shop, rider: dict, amount: Decimal, client: Any | None = None) -> dict:
    """Keeper /reconcilecod — rider hands over end-of-day cash. Appends the 'handover' row and
    returns the full trail (previous balance + today COD − handed over = remaining). The same
    trail is pushed to the rider so both sides hold the identical record."""
    if amount < 0:
        raise ValueError("handover amount can't be negative")
    today_start = _day(datetime.now(DUBAI).date())
    trail = cod_trail(await cod_rows(shop.id, rider["id"], client), today_start)

    sb = _sb(client)

    def _q() -> None:
        sb.table("cod_ledger").insert(
            {
                "shop_id": str(shop.id), "rider_id": rider["id"], "order_id": None,
                "entry": "handover", "amount": str(amount), "note": "end-of-day reconcile",
            }
        ).execute()

    await asyncio.to_thread(_q)
    trail["handover"] = amount
    trail["remaining"] = trail["balance"] - amount

    text = (
        f"🧾 COD reconcile — {rider['name']} @ {shop.name}\n"
        f"Previous balance: {trail['previous']} AED\n"
        f"+ Today collected: {trail['today_collect']} AED\n"
        + (f"− Earlier handover today: {trail['today_handover']} AED\n" if trail["today_handover"] else "")
        + f"− Handed over now: {amount} AED\n"
        f"= Remaining with rider: {trail['remaining']} AED"
    )
    trail["text"] = text
    if rider.get("telegram_id"):
        from app.telegram_bot.notify import send_to_rider

        await send_to_rider(int(rider["telegram_id"]), text)
    return trail


if __name__ == "__main__":
    # Pure-logic self-checks (no DB): phone normalization, custody, deliverability, cash, COD math.
    assert _normalize_phone("0501234567") == _normalize_phone("+971501234567") == "501234567"
    assert custody_transition("offered", True) == "accepted"
    assert custody_transition("none", False) == "disputed"
    try:
        custody_transition("accepted", False)
        raise AssertionError("re-deciding custody must fail")
    except ValueError:
        pass
    assert deliverable("shipped", "accepted") is None
    assert deliverable("shipped", "offered") is not None      # must /accept first
    assert deliverable("delivered", "accepted") is not None   # already done
    assert parse_cash("3,400") == Decimal("3400")
    t0 = _day(date(2026, 7, 12))
    rows = [
        {"entry": "collect", "amount": "500", "created_at": "2026-07-11T10:00:00+00:00"},
        {"entry": "handover", "amount": "300", "created_at": "2026-07-11T18:00:00+00:00"},
        {"entry": "collect", "amount": "3400", "created_at": "2026-07-12T09:00:00+04:00"},
    ]
    t = cod_trail(rows, t0)
    assert (t["previous"], t["today_collect"], t["balance"]) == (Decimal("200"), Decimal("3400"), Decimal("3600"))
    print("riders self-check ok")
