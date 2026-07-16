"""Orders + profit aggregation (SPEC §6). Tenant-scoped, like every other service here.

`create_order` is the only writer of the `orders` table. There is no customer-facing booking
flow yet (SPEC never specs one), so today orders are created by this function directly — used by
tests and whatever booking UX lands later. `profit_summary` is the read side the reports use.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.orders.models import ProfitLine, ProfitSummary, line_profit
from app.products.service import get_product
from app.telegram_bot.notify import send_to_customer, send_to_shopkeepers
from app.tenants.models import Shop

logger = logging.getLogger(__name__)

# ponytail: fetch the range's orders and aggregate in Python (mirrors products/search.py).
# ceiling: O(orders in range) per report. upgrade: a Postgres RPC/materialized view once a
# shop does thousands of orders a day.
_CLEARANCE = "clearance"

# Post-confirmation fulfilment chain (SPEC §6 delivery). A shopkeeper advances an order one step
# at a time; the customer is told at each step. Statuses match the orders.status CHECK constraint.
_DELIVERY_FLOW = ("confirmed", "packed", "shipped", "delivered")
_DELIVERY_MSG = {
    "packed": "📦 Order #{num} is packed and ready to go.",
    "shipped": "🚚 Order #{num} is on its way to you!",
    "delivered": "✅ Order #{num} has been delivered. Thank you for shopping with {shop}! 🙏",
}


def _is_next_step(current: str, target: str) -> bool:
    """True iff `target` is the immediate next fulfilment step after `current` (SPEC §6).

    Pure: no skipping (confirmed→delivered), no going backwards, nothing from outside the chain
    (a draft/cancelled order). This is the whole delivery-transition rule, unit-testable.
    """
    return (
        target in _DELIVERY_MSG
        and current in _DELIVERY_FLOW
        and _DELIVERY_FLOW.index(target) == _DELIVERY_FLOW.index(current) + 1
    )


class NoPendingDraft(Exception):
    """`/confirmorder` or `/rejectorder` for an order this shop has no pending draft of."""


class OrderNotFound(Exception):
    """No order with this number in this shop (unknown, or another shop's)."""


class InvalidTransition(Exception):
    """A delivery-status change that isn't the next step in the fulfilment chain."""


class OutOfStock(Exception):
    """Stock ran out between draft and confirmation — the atomic decrement changed no row."""


def _sb(client: Any | None) -> Any:
    from app.db.supabase_client import get_supabase

    return client if client is not None else get_supabase()


async def create_order(
    shop_id: UUID,
    *,
    customer_name: str,
    phone: str,
    address: str,
    product_id: UUID,
    quantity: int,
    selling_price: Decimal,
    discount_amount: Decimal = Decimal("0"),
    status: str = "pending",
    delivery_date: str | None = None,
    rider_id: UUID | None = None,
    special_instructions: str | None = None,
    client: Any | None = None,
) -> dict:
    """Insert one order + its first status-history row. Returns the inserted row (incl. order_number).

    The product must belong to this shop — `get_product` is the tenant guard (raises
    ProductNotFound for an unknown id or another shop's product, same message for both).
    """
    await get_product(shop_id, product_id, client)  # tenant guard; raises if not this shop's
    sb = _sb(client)

    row = {
        "shop_id": str(shop_id),
        "customer_name": customer_name,
        "phone": phone,
        "address": address,
        "product_id": str(product_id),
        "quantity": quantity,
        "selling_price": str(selling_price),
        "discount_amount": str(discount_amount),
        "status": status,
        "delivery_date": delivery_date,
        "rider_id": str(rider_id) if rider_id else None,
        "special_instructions": special_instructions,
    }

    def _q() -> dict:
        created = sb.table("orders").insert(row).execute().data[0]
        sb.table("order_status_history").insert(
            {"order_id": created["id"], "status": status, "changed_by": "system"}
        ).execute()
        return created

    return await asyncio.to_thread(_q)


async def profit_summary(
    shop_id: UUID, start: datetime, end: datetime, client: Any | None = None
) -> ProfitSummary:
    """Aggregate profit for a shop over [start, end). Cancelled orders excluded (SPEC §6)."""
    sb = _sb(client)

    def _q() -> list[dict]:
        r = (
            sb.table("orders")
            .select("quantity,selling_price,discount_amount,products(cost_price,brand,model,tags)")
            .eq("shop_id", str(shop_id))
            .gte("created_at", start.isoformat())
            .lt("created_at", end.isoformat())
            .neq("status", "cancelled")
            .neq("status", "draft")  # an unconfirmed draft is not revenue
            .execute()
        )
        return r.data or []

    return _aggregate(await asyncio.to_thread(_q))


async def product_stats(
    shop_id: UUID, start: datetime, end: datetime, client: Any | None = None
) -> list[dict]:
    """Per-product sales for `/productstats` (Q-014 — was a stub; nothing was ever tracked).

    Two reads, folded in Python: the orders in [start, end) (same exclusions as profit_summary, so
    the numbers agree with /profit) and the shop's full catalogue — a product that sold NOTHING is
    the most interesting row on the report, so it must appear with zeros.
    """
    sb = _sb(client)

    def _q() -> tuple[list[dict], list[dict]]:
        orders = (
            sb.table("orders")
            .select("product_id,quantity,selling_price,discount_amount,products(cost_price)")
            .eq("shop_id", str(shop_id))
            .gte("created_at", start.isoformat())
            .lt("created_at", end.isoformat())
            .neq("status", "cancelled")
            .neq("status", "draft")
            .execute()
            .data
            or []
        )
        products = (
            sb.table("products")
            .select("id,product_number,brand,model,quantity")
            .eq("shop_id", str(shop_id))
            .execute()
            .data
            or []
        )
        return orders, products

    orders, products = await asyncio.to_thread(_q)
    return _fold_product_stats(orders, products)


def _fold_product_stats(orders: list[dict], products: list[dict]) -> list[dict]:
    """Pure: orders + catalogue → one row per product, best seller first. Revenue is gross
    selling_price (what /profit calls revenue) so the two reports never disagree."""
    from app.utils.codes import product_code

    sold: dict[str, dict] = {}
    for o in orders:
        p = o.get("products") or {}
        sell = Decimal(str(o["selling_price"]))
        disc = Decimal(str(o["discount_amount"]))
        cp = Decimal(str(p.get("cost_price", "0")))
        qty = int(o["quantity"])
        e = sold.setdefault(
            str(o.get("product_id")), {"sold_qty": 0, "revenue": Decimal("0"), "profit": Decimal("0")}
        )
        e["sold_qty"] += qty
        e["revenue"] += sell
        e["profit"] += line_profit(sell, disc, cp, qty)

    zero = {"sold_qty": 0, "revenue": Decimal("0"), "profit": Decimal("0")}
    rows = []
    for pr in products:
        s = sold.get(str(pr.get("id")), zero)
        n = pr.get("product_number")
        rows.append({
            "code": product_code(n) if n else "—",
            "label": f"{pr.get('brand', '?')} {pr.get('model', '')}".strip(),
            "sold_qty": s["sold_qty"],
            "revenue": s["revenue"],
            "profit": s["profit"],
            "stock": int(pr.get("quantity") or 0),
        })
    rows.sort(key=lambda r: (r["revenue"], r["sold_qty"]), reverse=True)
    return rows


def _aggregate(rows: list[dict]) -> ProfitSummary:
    revenue = discounts = cost = profit = clearance = Decimal("0")
    by_product: dict[str, ProfitLine] = {}

    for o in rows:
        p = o.get("products") or {}
        sell = Decimal(str(o["selling_price"]))
        disc = Decimal(str(o["discount_amount"]))
        cp = Decimal(str(p.get("cost_price", "0")))
        qty = int(o["quantity"])
        pr = line_profit(sell, disc, cp, qty)

        revenue += sell
        discounts += disc
        cost += cp * qty
        profit += pr
        if _CLEARANCE in (p.get("tags") or []):
            clearance += pr

        label = f"{p.get('brand', '?')} {p.get('model', '?')}".strip()
        prev = by_product.get(label)
        by_product[label] = ProfitLine(
            label, (prev.qty if prev else 0) + qty, (prev.profit if prev else Decimal("0")) + pr
        )

    top = sorted(by_product.values(), key=lambda l: l.profit, reverse=True)[:5]
    return ProfitSummary(len(rows), revenue, discounts, cost, profit, clearance, top)


# ---------------------------------------------------------------------------
# Hybrid booking (Q-017): AI drafts, shopkeeper confirms.
# ---------------------------------------------------------------------------
_DRAFT_SELECT = "*, products(brand,model,color)"


async def _shopkeepers(shop_id: UUID) -> list:
    from app.db.factory import get_tenant_repo

    return await get_tenant_repo().list_shopkeepers(shop_id)


async def draft_order(
    shop: Shop,
    identity: str,
    *,
    product_id: UUID,
    quantity: int,
    customer_name: str,
    address: str,
    delivery_date: str | None = None,
    special_instructions: str | None = None,
    client: Any | None = None,
) -> dict:
    """AI-side: validate stock, apply any shop-APPROVED price, write a draft, notify the shop.

    The AI can never discount on its own (ADR-010 rev.): a discount exists only if a shopkeeper
    approved a `price_request` for this customer + product. The model is not told the order number —
    the customer learns it only on confirm (design #2).
    """
    product = await get_product(shop.id, product_id, client)  # tenant guard (raises ProductNotFound)
    if quantity <= 0:
        return {"error": "bad_quantity"}
    if product.quantity < quantity:  # inventory check BEFORE the shop is bothered (design #4)
        return {"error": "out_of_stock", "available": product.quantity}

    list_unit = product.selling_price
    approved = await _approved_price(shop.id, identity, product.id, client)  # None unless a human said yes
    unit = approved if approved is not None else list_unit

    await _cancel_pending_drafts(shop.id, identity, client)  # supersede an earlier draft (design #3)
    discount = (list_unit - unit) * quantity
    row = await create_order(
        shop.id, customer_name=customer_name, phone=identity, address=address,
        product_id=product.id, quantity=quantity, selling_price=list_unit * quantity,
        discount_amount=discount, status="draft", delivery_date=delivery_date,
        special_instructions=special_instructions, client=client,
    )

    num = row["order_number"]
    colour = f" {product.color}" if product.color else ""
    cost_total = product.cost_price * quantity
    charge = unit * quantity
    from app.telegram_bot.keyboards import keeper_order_actions as _draft_actions

    await _notify_shop(
        shop,
        f"🧾 New order draft #{num}\n"
        f"{customer_name} ({identity})\n"
        f"{product.brand} {product.model}{colour} ×{quantity}\n"
        f"Buy (cost): {cost_total} AED\n"
        f"List: {list_unit * quantity} AED\n"
        + (f"Discount: {discount} AED\n" if discount else "")
        + f"Charge: {charge} AED\n"
        f"Margin: {charge - cost_total} AED\n"
        f"Deliver: {address}"
        + (f"\nWhen: {delivery_date}" if delivery_date else "")
        + (f"\nNote: {special_instructions}" if special_instructions else "")
        + f"\n\nTap below — or /confirmorder {num} / /rejectorder {num} [reason]",
        _draft_actions(num),
    )
    return {"status": "submitted_to_shop"}


# --- price negotiation (ADR-010 rev.): AI asks, shopkeeper approves. No autonomous discount. ---
class NoPriceRequest(Exception):
    """`/approveprice` / `/denyprice` / `/custom` for a request this shop has no pending one of."""


async def request_price(
    shop: Shop, identity: str, product_id: UUID, requested_price: Decimal, client: Any | None = None
) -> dict:
    """Customer haggled. Raise a price request for the shop to decide — never quote a discount here.

    Idempotent so the model can't flood duplicates (it re-asked and made #3/#4 in testing):
    - already an APPROVED price for this customer+product → tell the model to just place the order;
    - already a PENDING request → say it's still with the shop, don't open a second one.
    """
    # Read the toggle FRESH, not from the (startup-snapshot) shop object: a shop that just turned
    # negotiation off must be respected on the very next haggle. "When off, do not give a discount."
    if not await _negotiation_on(shop.id, client):
        return {"error": "negotiation_off"}  # shop opted out → the AI must hold at list price
    product = await get_product(shop.id, product_id, client)  # tenant guard + list/cost for the notice
    if requested_price <= 0:  # a haggle for 0 or negative is not a real offer (guard bad LLM args)
        return {"error": "bad_price"}

    approved = await _approved_price(shop.id, identity, product.id, client)
    if approved is not None:  # shop already said yes — steer the model to book, not re-ask
        return {"status": "already_approved", "price_aed": str(approved)}
    if await _pending_price_request(shop.id, identity, product.id, client) is not None:
        return {"status": "asked_shop"}  # one open request per customer+product — no duplicate

    row = await _open_price_request(shop.id, identity, product.id, requested_price, client)
    num = row["request_number"]
    from app.telegram_bot.keyboards import keeper_price_actions

    await _notify_shop(
        shop,
        f"💰 Price request #{num}\n"
        f"{identity} — {product.brand} {product.model}\n"
        f"Offer: {requested_price} AED\n"
        f"List: {product.selling_price} AED\n"
        f"Buy (cost): {product.cost_price} AED\n"
        f"Margin if approved: {requested_price - product.cost_price} AED\n\n"
        f"Tap below (Counter asks your price) — or /approveprice {num} / /custom {num} <price> "
        f"/ /denyprice {num}",
        keeper_price_actions(num),
    )
    return {"status": "asked_shop"}


async def approve_price(
    shop: Shop, request_number: int, custom_price: Decimal | None = None, client: Any | None = None
) -> Decimal:
    """Shopkeeper accepts (or counters with `custom_price`). Tells the customer the price. Returns it.

    An approved price must be a real discount: `0 < price <= list`. This blocks a negative/zero
    charge and an above-list "discount" (which would make `discount_amount` negative and violate the
    DB check) — a business-rule guard on the money path.
    """
    req = await _get_price_request(shop.id, request_number, client)
    price = Decimal(str(req["requested_price"])) if custom_price is None else custom_price
    product = await get_product(shop.id, UUID(req["product_id"]), client)
    if not (Decimal("0") < price <= product.selling_price):
        raise ValueError(f"price must be between 0 and the list price ({product.selling_price} AED)")
    await _set_price_status(req["id"], "approved", price, client)
    msg = f"Good news — we can do it for {price} AED. 🙌"
    await send_to_customer(shop, req["phone"], msg)
    await _remember_to_customer(shop, req["phone"], msg)  # so the AI knows it's approved (not re-ask)
    return price


async def deny_price(shop: Shop, request_number: int, client: Any | None = None) -> None:
    """Shopkeeper declines. Tell the customer the list price is the best available."""
    req = await _get_price_request(shop.id, request_number, client)
    await _set_price_status(req["id"], "denied", None, client)
    product = await get_product(shop.id, UUID(req["product_id"]), client)
    msg = f"{product.selling_price} AED is the best price we can do on this one."
    await send_to_customer(shop, req["phone"], msg)
    await _remember_to_customer(shop, req["phone"], msg)  # so the AI holds at list, not re-ask


async def _remember_to_customer(shop: Shop, phone: str, text: str) -> None:
    """Record a shop→customer message in the AI session so the assistant knows it was said and
    doesn't re-ask (mirrors escalations.reply). Best-effort — never breaks the command."""
    try:
        from app.db.redis_client import get_redis
        from app.escalations.context import remember

        await remember(get_redis(), shop.id, phone, "assistant", text)
    except Exception:
        logger.exception("remember shop→customer failed shop=%s", shop.id)


async def _pending_price_request(
    shop_id: UUID, identity: str, product_id: UUID, client: Any | None
) -> dict | None:
    """The open (pending) request for this customer+product, if any — dedup guard for request_price."""
    sb = _sb(client)

    def _q() -> dict | None:
        rows = (
            sb.table("price_requests").select("id")
            .eq("shop_id", str(shop_id)).eq("phone", identity).eq("product_id", str(product_id))
            .eq("status", "pending").limit(1).execute().data or []
        )
        return rows[0] if rows else None

    return await asyncio.to_thread(_q)


async def set_negotiation(shop_id: UUID, enabled: bool, client: Any | None = None) -> None:
    """`/negotiation on|off` — toggle whether the AI may raise price requests for this shop."""
    sb = _sb(client)

    def _q() -> None:
        sb.table("shops").update({"negotiation_enabled": enabled}).eq("id", str(shop_id)).execute()

    await asyncio.to_thread(_q)


async def _negotiation_on(shop_id: UUID, client: Any | None) -> bool:
    sb = _sb(client)

    def _q() -> bool:
        rows = (
            sb.table("shops").select("negotiation_enabled").eq("id", str(shop_id)).limit(1).execute().data
            or []
        )
        return bool(rows[0]["negotiation_enabled"]) if rows else True

    return await asyncio.to_thread(_q)


async def _open_price_request(
    shop_id: UUID, identity: str, product_id: UUID, requested_price: Decimal, client: Any | None
) -> dict:
    sb = _sb(client)

    def _q() -> dict:
        return sb.table("price_requests").insert(
            {
                "shop_id": str(shop_id), "phone": identity, "product_id": str(product_id),
                "requested_price": str(requested_price),
            }
        ).execute().data[0]

    return await asyncio.to_thread(_q)


async def _get_price_request(shop_id: UUID, request_number: int, client: Any | None) -> dict:
    sb = _sb(client)

    def _q() -> dict | None:
        rows = (
            sb.table("price_requests").select("*")
            .eq("shop_id", str(shop_id)).eq("request_number", request_number).eq("status", "pending")
            .limit(1).execute().data or []
        )
        return rows[0] if rows else None

    req = await asyncio.to_thread(_q)
    if req is None:
        raise NoPriceRequest(request_number)
    return req


async def _set_price_status(
    req_id: str, status: str, approved_price: Decimal | None, client: Any | None
) -> None:
    sb = _sb(client)

    def _q() -> None:
        patch: dict[str, Any] = {"status": status}
        if approved_price is not None:
            patch["approved_price"] = str(approved_price)
        sb.table("price_requests").update(patch).eq("id", req_id).execute()

    await asyncio.to_thread(_q)


async def _approved_price(
    shop_id: UUID, identity: str, product_id: UUID, client: Any | None
) -> Decimal | None:
    """The most recent shop-approved price for this customer + product, if any."""
    sb = _sb(client)

    def _q() -> Decimal | None:
        rows = (
            sb.table("price_requests").select("approved_price")
            .eq("shop_id", str(shop_id)).eq("phone", identity).eq("product_id", str(product_id))
            .eq("status", "approved").order("request_number", desc=True).limit(1).execute().data or []
        )
        return Decimal(str(rows[0]["approved_price"])) if rows and rows[0]["approved_price"] else None

    return await asyncio.to_thread(_q)


async def confirm_order(shop: Shop, order_number: int, client: Any | None = None) -> dict:
    """Shopkeeper accepts a draft: atomic stock decrement, mark confirmed, tell the customer."""
    draft = await _get_draft(shop.id, order_number, client)
    if not await _decrement_stock(shop.id, draft["product_id"], draft["quantity"], client):
        raise OutOfStock(order_number)  # sold out between draft and confirm — nothing oversold
    await _set_status(draft["id"], "confirmed", "system", client)

    net = Decimal(str(draft["selling_price"])) - Decimal(str(draft["discount_amount"]))
    p = draft.get("products") or {}
    name = f"{p.get('brand', '')} {p.get('model', '')}".strip() or "your order"
    msg = (
        f"✅ Order #{order_number} confirmed!\n"
        f"{draft['quantity']}× {name} — {net} AED\n"
        f"Deliver to: {draft['address']}"
        + (f"\nDelivery: {draft['delivery_date']}" if draft.get("delivery_date") else "")
        + "\nThank you! 🙏"
    )
    await send_to_customer(shop, draft["phone"], msg)
    await _remember_to_customer(shop, draft["phone"], msg)  # AI knows it's confirmed (not "still waiting")
    await notify_low_stock(shop, draft["product_id"], client)  # only after a successful decrement
    return draft


async def notify_low_stock(shop: Shop, product_id: Any, client: Any | None = None) -> bool:
    """Ping the shop AND its owner when a product just fell to its min_qty (migration 010).

    Called only after stock actually went DOWN (confirm_order, counter sales) — a restock must
    never alert. min_qty = 0 means alerts are off, which is every product until someone sets one.
    Best-effort: an alert that fails can never break the sale that triggered it.
    """
    try:
        from app.products.service import get_product

        product = await get_product(shop.id, product_id, client)  # fresh read: post-decrement qty
        if product.min_qty <= 0 or product.quantity > product.min_qty:
            return False

        from app.utils.codes import product_code

        ref = product_code(product.product_number) if product.product_number else str(product.id)
        text = (
            f"⚠️ Low stock — {shop.name}\n"
            f"{ref} · {product.brand} {product.model}\n"
            f"{product.quantity} left (alert at {product.min_qty}). Time to reorder."
        )
        await _notify_shop(shop, text)

        from app.db.factory import get_tenant_repo
        from app.telegram_bot.notify import send_to_shopowner

        owner = await get_tenant_repo().get_client(shop.client_id)
        if owner is not None and owner.telegram_id:
            await send_to_shopowner(owner.telegram_id, text)
        return True
    except Exception:
        logger.exception("low-stock alert failed shop=%s product=%s", shop.id, product_id)
        return False


async def advance_delivery(
    shop: Shop, order_number: int, new_status: str, client: Any | None = None
) -> dict:
    """`/deliveryupdate <#> packed|shipped|delivered` — move one order one step down the fulfilment
    chain and tell the customer. Rejects any move that isn't the immediate next step (SPEC §6):
    no skipping (confirmed→delivered), no going backwards, no touching a draft/cancelled order.
    """
    target = (new_status or "").strip().lower()
    if target not in _DELIVERY_MSG:  # only packed|shipped|delivered are valid destinations
        raise InvalidTransition("status must be one of: packed, shipped, delivered")
    order = await _get_order(shop.id, order_number, client)
    current = order["status"]
    if not _is_next_step(current, target):
        raise InvalidTransition(f"cannot move order #{order_number} from '{current}' to '{target}'")

    await _set_status(order["id"], target, "shopkeeper", client)
    msg = _DELIVERY_MSG[target].format(num=order_number, shop=shop.name)
    await send_to_customer(shop, order["phone"], msg)
    await _remember_to_customer(shop, order["phone"], msg)  # AI stays in sync with fulfilment state
    return order


# Statuses at which a rider may be assigned: the order is a real, confirmed order still in flight.
# Not a draft (unconfirmed), not delivered (done), not cancelled.
_ASSIGNABLE = ("confirmed", "packed", "shipped")


async def assign_delivery(
    shop: Shop, order_number: int, rider_id: UUID, client: Any | None = None
) -> dict:
    """`/assigndelivery <#> <rider_id>` — attach a rider to a confirmed order and push them the
    delivery details. Both order and rider are tenant-guarded (must belong to this shop). Returns
    `{rider, notified}` so the shopkeeper is told whether the rider has linked Telegram yet.
    """
    from app.riders.service import cod_balance, get_rider
    from app.telegram_bot.keyboards import rider_delivery_actions
    from app.telegram_bot.notify import send_to_rider

    order = await _get_order(shop.id, order_number, client)  # raises OrderNotFound
    if order["status"] not in _ASSIGNABLE:
        raise InvalidTransition(
            f"order #{order_number} is '{order['status']}' — assign a rider to a confirmed order"
        )
    rider = await get_rider(shop.id, rider_id, client)  # raises RiderNotFound

    # COD = the net charge; custody goes to 'offered' — the rider must /accept the pickup.
    cod = Decimal(str(order["selling_price"])) - Decimal(str(order["discount_amount"]))
    await _set_rider(order["id"], rider_id, cod, client)

    notified = False
    if rider.get("telegram_id"):
        outstanding = await cod_balance(shop.id, rider_id, client)  # cash they already hold
        p = order.get("products") or {}
        name = f"{p.get('brand', '')} {p.get('model', '')}".strip() or "item"
        text = (
            f"🛵 New delivery — order #{order_number} ({shop.name})\n"
            f"Customer: {order['customer_name']} ({order['phone']})\n"
            f"Item: {name} ×{order['quantity']}\n"
            f"Address: {order['address']}"
            + (f"\nWhen: {order['delivery_date']}" if order.get("delivery_date") else "")
            + (f"\nNote: {order['special_instructions']}" if order.get("special_instructions") else "")
            + f"\n\n💵 Collect (COD): {cod} AED"
            f"\n📊 Cash you already hold: {outstanding} AED"
            f"\n\nTap below — or /accept {order_number} / /notreceived {order_number}"
        )
        notified = await send_to_rider(
            int(rider["telegram_id"]), text,
            reply_markup=rider_delivery_actions(order_number, "offered", order["status"]),
        )
    return {"rider": rider, "notified": notified, "cod": cod}


async def reject_order(
    shop: Shop, order_number: int, reason: str | None = None, client: Any | None = None
) -> dict:
    """Shopkeeper declines a draft. The customer is NOT cold-messaged 'rejected' (design #2) —
    the AI keeps serving them and staff can `/reply` if they want to explain."""
    draft = await _get_draft(shop.id, order_number, client)
    await _set_status(draft["id"], "cancelled", reason or "rejected by shop", client)
    return draft


async def list_drafts(shop_id: UUID, client: Any | None = None) -> list[dict]:
    """Pending drafts for `/orders`."""
    sb = _sb(client)

    def _q() -> list[dict]:
        return (
            sb.table("orders").select(_DRAFT_SELECT)
            .eq("shop_id", str(shop_id)).eq("status", "draft")
            .order("order_number").execute().data or []
        )

    return await asyncio.to_thread(_q)


async def list_price_requests(shop_id: UUID, client: Any | None = None) -> list[dict]:
    """Pending price requests for the keeper's 💰 button (joins the product for a readable line)."""
    sb = _sb(client)

    def _q() -> list[dict]:
        return (
            sb.table("price_requests")
            .select("request_number,phone,requested_price,products(brand,model,selling_price)")
            .eq("shop_id", str(shop_id)).eq("status", "pending")
            .order("request_number").execute().data or []
        )

    return await asyncio.to_thread(_q)


# ---------------------------------------------------------------------------
# Shop-owner anti-corruption reads: cancellations (with remarks) + discounts.
# The owner audits remotely; these expose exactly what a keeper could hide.
# ---------------------------------------------------------------------------
async def cancelled_orders(
    shop_id: UUID, start: datetime, end: datetime, client: Any | None = None
) -> list[dict]:
    """Cancelled orders in a window. Remarks live in TWO places: orders.cancel_remarks (rider
    cancels) and the 'cancelled' history row's changed_by (shop rejections via _set_status)."""
    sb = _sb(client)

    def _q() -> list[dict]:
        return (
            sb.table("orders")
            .select("*, products(brand,model), order_status_history(status,changed_by,changed_at)")
            .eq("shop_id", str(shop_id)).eq("status", "cancelled")
            .gte("created_at", start.isoformat()).lt("created_at", end.isoformat())
            .order("order_number").execute().data or []
        )

    return await asyncio.to_thread(_q)


async def discounted_orders(
    shop_id: UUID, start: datetime, end: datetime, client: Any | None = None
) -> list[dict]:
    """Every non-draft order with a discount in a window (who gave money away, and how much)."""
    sb = _sb(client)

    def _q() -> list[dict]:
        return (
            sb.table("orders")
            .select("*, products(brand,model)")
            .eq("shop_id", str(shop_id)).neq("status", "draft").gt("discount_amount", 0)
            .gte("created_at", start.isoformat()).lt("created_at", end.isoformat())
            .order("order_number").execute().data or []
        )

    return await asyncio.to_thread(_q)


# ---------------------------------------------------------------------------
# Excel export (SPEC §10): fetch the shop's orders → workbook → signed URL.
# ---------------------------------------------------------------------------
_EXPORT_SELECT = "*, products(category,brand,model,color,specs)"
_RIDER_SELECT = "*, products(category,brand,model,color,specs), delivery_persons(name,phone)"


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


async def orders_for_export(shop_id: UUID, filter_arg: str, client: Any | None = None) -> list[dict]:
    """Orders for `/exportorders today|yesterday|YYYY-MM-DD|pending|all`. Drafts never exported."""
    from app.reports.service import parse_period  # reuse the /profit period parser (today/yesterday/date)

    sb = _sb(client)
    filt = (filter_arg or "today").strip().lower()

    def _q() -> list[dict]:
        q = sb.table("orders").select(_EXPORT_SELECT).eq("shop_id", str(shop_id)).neq("status", "draft")
        if filt == "all":
            pass
        elif filt == "pending":
            q = q.eq("status", "confirmed")  # confirmed but not yet delivered = the pick queue
        else:
            start, end, _ = parse_period(filt)  # raises ValueError on junk → safe keeper reply
            q = q.gte("created_at", start.isoformat()).lt("created_at", end.isoformat())
        return q.order("order_number").execute().data or []

    return await asyncio.to_thread(_q)


async def orders_in_range(
    shop_id: UUID, start: datetime, end: datetime, client: Any | None = None
) -> list[dict]:
    """Range twin of orders_for_export: every non-draft order in [start, end), oldest first.

    The shop owner's 🗓 Date range view groups these by day, so order by created_at, not number.
    """
    sb = _sb(client)

    def _q() -> list[dict]:
        return (
            sb.table("orders")
            .select(_EXPORT_SELECT)
            .eq("shop_id", str(shop_id))
            .neq("status", "draft")
            .gte("created_at", start.isoformat())
            .lt("created_at", end.isoformat())
            .order("created_at")
            .execute()
            .data
            or []
        )

    return await asyncio.to_thread(_q)


async def rider_orders_for_export(
    shop_id: UUID, rider_id: UUID, filter_arg: str, client: Any | None = None
) -> list[dict]:
    """One rider's orders, sorted by address (SPEC §10 `/exportrider`)."""
    from app.reports.service import parse_period

    sb = _sb(client)
    start, end, _ = parse_period(filter_arg or "today")

    def _q() -> list[dict]:
        return (
            sb.table("orders").select(_RIDER_SELECT)
            .eq("shop_id", str(shop_id)).eq("rider_id", str(rider_id)).neq("status", "draft")
            .gte("created_at", start.isoformat()).lt("created_at", end.isoformat())
            .order("address").execute().data or []
        )

    return await asyncio.to_thread(_q)


async def export_orders(
    shop: Shop, filter_arg: str, detailed: bool = False, client: Any | None = None
) -> tuple[str, str, int]:
    """Build + upload the pick-&-pack sheet. Returns (filename, 24h signed URL, row count)."""
    from app.utils.excel import orders_workbook
    from app.utils.storage import upload_report

    filt = (filter_arg or "today").strip().lower()
    rows = await orders_for_export(shop.id, filt, client)
    name = f"orders_{filt}_{_stamp()}.xlsx"
    url = await upload_report(shop.id, name, orders_workbook(rows, detailed=detailed), client)
    return name, url, len(rows)


async def export_rider(
    shop: Shop, rider_id: UUID, filter_arg: str, client: Any | None = None
) -> tuple[str, str, int]:
    """Build + upload a rider's route sheet (always detailed — it needs rider + instructions)."""
    from app.utils.excel import orders_workbook
    from app.utils.storage import upload_report

    filt = (filter_arg or "today").strip().lower()
    rows = await rider_orders_for_export(shop.id, rider_id, filt, client)
    name = f"rider_{rider_id}_{filt}_{_stamp()}.xlsx"
    url = await upload_report(shop.id, name, orders_workbook(rows, detailed=True), client)
    return name, url, len(rows)


# --- internals ---
async def _get_draft(shop_id: UUID, order_number: int, client: Any | None) -> dict:
    sb = _sb(client)

    def _q() -> dict | None:
        rows = (
            sb.table("orders").select(_DRAFT_SELECT)
            .eq("shop_id", str(shop_id)).eq("order_number", order_number).eq("status", "draft")
            .limit(1).execute().data or []
        )
        return rows[0] if rows else None

    draft = await asyncio.to_thread(_q)
    if draft is None:
        raise NoPendingDraft(order_number)  # unknown, another shop's, or already decided
    return draft


async def _get_order(shop_id: UUID, order_number: int, client: Any | None) -> dict:
    """Fetch one order by number within this shop, any status (delivery updates act post-confirm)."""
    sb = _sb(client)

    def _q() -> dict | None:
        rows = (
            sb.table("orders").select(_DRAFT_SELECT)
            .eq("shop_id", str(shop_id)).eq("order_number", order_number)
            .limit(1).execute().data or []
        )
        return rows[0] if rows else None

    order = await asyncio.to_thread(_q)
    if order is None:
        raise OrderNotFound(order_number)  # unknown or another shop's — same message either way
    return order


async def _cancel_pending_drafts(shop_id: UUID, identity: str, client: Any | None) -> None:
    sb = _sb(client)

    def _q() -> None:
        sb.table("orders").update({"status": "cancelled"}).eq("shop_id", str(shop_id)).eq(
            "phone", identity
        ).eq("status", "draft").execute()

    await asyncio.to_thread(_q)


async def _set_status(order_id: str, status: str, changed_by: str, client: Any | None) -> None:
    sb = _sb(client)

    def _q() -> None:
        sb.table("orders").update({"status": status}).eq("id", order_id).execute()
        sb.table("order_status_history").insert(
            {"order_id": order_id, "status": status, "changed_by": changed_by}
        ).execute()

    await asyncio.to_thread(_q)


async def _set_rider(order_id: str, rider_id: UUID, cod: Decimal, client: Any | None) -> None:
    sb = _sb(client)

    def _q() -> None:
        sb.table("orders").update(
            {"rider_id": str(rider_id), "cod_amount": str(cod), "custody": "offered"}
        ).eq("id", order_id).execute()

    await asyncio.to_thread(_q)


async def _decrement_stock(shop_id: UUID, product_id: str, qty: int, client: Any | None) -> bool:
    """Atomic (SPEC inventory). The DB RPC decrements only if `quantity >= qty` (migration 003)."""
    sb = _sb(client)

    def _q() -> bool:
        r = sb.rpc(
            "decrement_stock", {"p_id": product_id, "p_shop": str(shop_id), "n": qty}
        ).execute()
        return bool(r.data)

    return await asyncio.to_thread(_q)


async def _notify_shop(shop: Shop, text: str, reply_markup=None) -> None:
    """Best-effort staff notification (never raises — mirrors escalations)."""
    try:
        await send_to_shopkeepers(shop, await _shopkeepers(shop.id), text, reply_markup)
    except Exception:
        logger.exception("draft notify failed shop=%s", shop.id)
