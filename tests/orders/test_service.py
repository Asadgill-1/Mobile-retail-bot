"""Orders service (SPEC §6): profit aggregation + create_order tenant guard. Supabase faked."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

import app.orders.service as svc
from app.orders.models import ProfitSummary, line_profit
from app.orders.service import _aggregate, create_order, profit_summary


def _row(sell, disc, cost, qty, brand="Samsung", model="S23", tags=None):
    return {
        "quantity": qty,
        "selling_price": str(sell),
        "discount_amount": str(disc),
        "products": {"cost_price": str(cost), "brand": brand, "model": model, "tags": tags or []},
    }


# --- pure aggregation ---
def test_aggregate_totals_and_margin():
    rows = [_row(2499, 0, 2000, 1), _row(5000, 200, 2000, 2)]  # profit 499 + 800
    s = _aggregate(rows)
    assert s.orders == 2
    assert s.revenue == Decimal("7499") and s.discounts == Decimal("200")
    assert s.cost == Decimal("6000") and s.profit == Decimal("1299")
    assert round(s.margin, 2) == round(float(Decimal("1299") / Decimal("6000") * 100), 2)


def test_aggregate_clearance_and_top_grouping():
    rows = [
        _row(3000, 0, 2000, 1, model="A", tags=["clearance"]),  # +1000 clearance
        _row(1500, 0, 1000, 1, model="A"),                      # +500, same product
        _row(900, 0, 800, 1, model="B"),                        # +100
    ]
    s = _aggregate(rows)
    assert s.clearance_profit == Decimal("1000")
    assert s.top[0].label == "Samsung A" and s.top[0].qty == 2 and s.top[0].profit == Decimal("1500")
    assert s.top[1].label == "Samsung B"


def test_empty_range_is_a_zero_summary():
    s = _aggregate([])
    assert s == ProfitSummary() and s.margin == 0.0


# --- delivery transition rule (SPEC §6): only the immediate next step is allowed ---
@pytest.mark.parametrize(
    "current,target,ok",
    [
        ("confirmed", "packed", True),
        ("packed", "shipped", True),
        ("shipped", "delivered", True),
        ("confirmed", "shipped", False),     # skip
        ("confirmed", "delivered", False),   # skip
        ("delivered", "shipped", False),     # backward
        ("delivered", "confirmed", False),   # backward + not a valid destination
        ("draft", "packed", False),          # not yet in the flow
        ("cancelled", "packed", False),      # out of the flow
        ("shipped", "shipped", False),       # no-op is not a step
    ],
)
def test_delivery_only_next_step(current, target, ok):
    assert svc._is_next_step(current, target) is ok


# --- create_order (tenant guard reused from products) ---
class _FakeSB:
    def __init__(self):
        self.inserts = []

    def table(self, name):
        self._t = name
        return self

    def insert(self, row):
        self.inserts.append((self._t, row))
        self._last = row
        return self

    def execute(self):
        class _R:
            data = [{"id": "order-1"}]

        return _R()


@pytest.mark.asyncio
async def test_create_order_checks_tenant_and_writes_status_history(monkeypatch):
    seen = {}

    async def _guard(shop_id, product_id, client):
        seen["guarded"] = (shop_id, product_id)  # get_product = the tenant guard

    monkeypatch.setattr(svc, "get_product", _guard)
    sb = _FakeSB()
    shop_id, pid = uuid4(), uuid4()

    row = await create_order(
        shop_id, customer_name="Ali", phone="p1", address="Marina",
        product_id=pid, quantity=2, selling_price=Decimal("5000"), client=sb,
    )

    assert row["id"] == "order-1"
    assert seen["guarded"] == (shop_id, pid)  # guard ran before insert
    tables = [t for t, _ in sb.inserts]
    assert tables == ["orders", "order_status_history"]
    assert sb.inserts[0][1]["shop_id"] == str(shop_id)  # shop_id forced, never user input
    assert sb.inserts[1][1]["status"] == "pending"


@pytest.mark.asyncio
async def test_profit_summary_reads_range_and_aggregates(monkeypatch):
    class _QSB:
        def table(self, n): return self
        def select(self, *a): return self
        def eq(self, *a): return self
        def gte(self, *a): return self
        def lt(self, *a): return self
        def neq(self, *a): return self
        def execute(self):
            class _R:
                data = [_row(2499, 0, 2000, 1)]
            return _R()

    start = datetime(2026, 7, 9, tzinfo=timezone.utc)
    end = datetime(2026, 7, 10, tzinfo=timezone.utc)
    s = await profit_summary(uuid4(), start, end, client=_QSB())
    assert s.orders == 1 and s.profit == Decimal("499")


def test_line_profit_multiplies_cost_by_quantity():
    assert line_profit(Decimal("5000"), Decimal("200"), Decimal("2000"), 2) == Decimal("800")


# --- hybrid booking + negotiation (Q-017 / ADR-010 rev.) ---
from app.orders.service import (  # noqa: E402
    OutOfStock,
    approve_price,
    confirm_order,
    deny_price,
    draft_order,
    reject_order,
    request_price,
)
from app.products.models import Product  # noqa: E402
from app.tenants.models import Shop as _Shop  # noqa: E402


def _shop_obj():
    return _Shop(id=uuid4(), client_id=uuid4(), name="Shop 01")


def _product(qty=5, sell="3400", cost="2800"):
    return Product(
        id=uuid4(), shop_id=uuid4(), category="Mobile", brand="Samsung", model="S23",
        color="green", condition="New", cost_price=Decimal(cost), selling_price=Decimal(sell),
        quantity=qty,
    )


@pytest.fixture
def draft_wire(monkeypatch):
    """Fake every DB/notify edge draft_order touches; capture what it would write/send."""
    cap = {"created": None, "notified": None, "cancelled": False, "approved": None}

    async def _cancel(shop_id, identity, client):
        cap["cancelled"] = True

    async def _create(shop_id, **kw):
        cap["created"] = kw
        return {"order_number": 7, "id": "o1"}

    async def _notify(shop, text, reply_markup=None):
        cap["notified"] = text

    async def _approved(shop_id, identity, product_id, client):
        return cap["approved"]  # None unless a test says the shop approved a price

    monkeypatch.setattr(svc, "_cancel_pending_drafts", _cancel)
    monkeypatch.setattr(svc, "create_order", _create)
    monkeypatch.setattr(svc, "_notify_shop", _notify)
    monkeypatch.setattr(svc, "_approved_price", _approved)
    return cap


@pytest.mark.asyncio
async def test_draft_order_in_stock_no_bargain(draft_wire, monkeypatch):
    prod = _product(qty=5, sell="3400")

    async def _get(shop_id, pid, client):
        return prod

    monkeypatch.setattr(svc, "get_product", _get)
    res = await draft_order(_shop_obj(), "p1", product_id=prod.id, quantity=2,
                            customer_name="Ali", address="Marina")

    assert res == {"status": "submitted_to_shop"}  # model is NOT told the order number
    assert draft_wire["created"]["selling_price"] == Decimal("6800")  # list × qty
    assert draft_wire["created"]["discount_amount"] == Decimal("0")
    assert draft_wire["created"]["status"] == "draft"
    assert draft_wire["cancelled"] is True  # superseded any earlier draft
    assert "#7" in draft_wire["notified"] and "/confirmorder 7" in draft_wire["notified"]


@pytest.mark.asyncio
async def test_draft_order_out_of_stock_never_reaches_the_shop(draft_wire, monkeypatch):
    async def _get(shop_id, pid, client):
        return _product(qty=1)

    monkeypatch.setattr(svc, "get_product", _get)
    res = await draft_order(_shop_obj(), "p1", product_id=uuid4(), quantity=3,
                            customer_name="Ali", address="Marina")

    assert res == {"error": "out_of_stock", "available": 1}
    assert draft_wire["created"] is None and draft_wire["notified"] is None


@pytest.mark.asyncio
async def test_draft_order_applies_only_a_shop_approved_price(draft_wire, monkeypatch):
    """The AI cannot discount on its own — the discount comes from a shop-approved price."""
    prod = _product(qty=5, sell="3400")

    async def _get(shop_id, pid, client):
        return prod

    monkeypatch.setattr(svc, "get_product", _get)
    draft_wire["approved"] = Decimal("3100")  # shopkeeper approved this earlier
    await draft_order(_shop_obj(), "p1", product_id=prod.id, quantity=1,
                      customer_name="Ali", address="Marina")

    assert draft_wire["created"]["selling_price"] == Decimal("3400")   # list
    assert draft_wire["created"]["discount_amount"] == Decimal("300")  # 3400 - approved 3100


# --- price negotiation loop (ADR-010 rev.) ---
@pytest.fixture
def price_wire(monkeypatch):
    cap = {"opened": None, "notified": None, "status": None, "customer": None, "on": True,
           "approved": None, "pending": None, "remembered": None}

    async def _on(shop_id, client):
        return cap["on"]

    async def _open(shop_id, identity, pid, price, client):
        cap["opened"] = price
        return {"request_number": 4, "id": "pr1"}

    async def _notify(shop, text, reply_markup=None):
        cap["notified"] = text

    async def _get_req(shop_id, num, client):
        return {"id": "pr1", "phone": "p1", "product_id": str(uuid4()), "requested_price": "3100"}

    async def _set(rid, status, price, client):
        cap["status"] = (status, price)

    async def _to_customer(shop, identity, text):
        cap["customer"] = (identity, text)
        return True

    async def _approved(shop_id, identity, pid, client):
        return cap["approved"]  # no prior approval by default

    async def _pending(shop_id, identity, pid, client):
        return cap["pending"]  # no open request by default

    async def _remember(shop, phone, text):
        cap["remembered"] = (phone, text)

    async def _get_product(shop_id, pid, client=None):
        return _product()  # list price 3400 — approve bound check (0 < price <= list) passes at 3100/3250

    monkeypatch.setattr(svc, "get_product", _get_product)
    monkeypatch.setattr(svc, "_negotiation_on", _on)
    monkeypatch.setattr(svc, "_open_price_request", _open)
    monkeypatch.setattr(svc, "_notify_shop", _notify)
    monkeypatch.setattr(svc, "_get_price_request", _get_req)
    monkeypatch.setattr(svc, "_set_price_status", _set)
    monkeypatch.setattr(svc, "send_to_customer", _to_customer)
    monkeypatch.setattr(svc, "_approved_price", _approved)
    monkeypatch.setattr(svc, "_pending_price_request", _pending)
    monkeypatch.setattr(svc, "_remember_to_customer", _remember)
    return cap


@pytest.mark.asyncio
async def test_request_price_asks_the_shop_when_on(price_wire, monkeypatch):
    prod = _product()

    async def _get(shop_id, pid, client):
        return prod

    monkeypatch.setattr(svc, "get_product", _get)
    res = await request_price(_shop_obj(), "p1", prod.id, Decimal("3100"))
    assert res == {"status": "asked_shop"}
    assert price_wire["opened"] == Decimal("3100")
    assert "#4" in price_wire["notified"] and "/approveprice 4" in price_wire["notified"]


@pytest.mark.asyncio
async def test_request_price_rejects_non_positive_offer(price_wire):
    res = await request_price(_shop_obj(), "p1", uuid4(), Decimal("0"))
    assert res == {"error": "bad_price"}
    assert price_wire["opened"] is None


@pytest.mark.asyncio
async def test_approve_price_rejects_above_list_or_non_positive(price_wire):
    with pytest.raises(ValueError):  # list is 3400; 5000 isn't a discount
        await approve_price(_shop_obj(), 4, Decimal("5000"))
    with pytest.raises(ValueError):
        await approve_price(_shop_obj(), 4, Decimal("0"))


@pytest.mark.asyncio
async def test_request_price_deduped_when_one_is_pending(price_wire, monkeypatch):
    """The model re-asked and made duplicate #3/#4 in testing — a pending request must not dup."""
    price_wire["pending"] = {"id": "pr1"}  # already an open request for this customer+product

    async def _get(shop_id, pid, client):
        return _product()

    monkeypatch.setattr(svc, "get_product", _get)
    res = await request_price(_shop_obj(), "p1", uuid4(), Decimal("3100"))
    assert res == {"status": "asked_shop"}
    assert price_wire["opened"] is None  # no second row opened
    assert price_wire["notified"] is None  # shop not pinged twice


@pytest.mark.asyncio
async def test_request_price_steers_to_order_when_already_approved(price_wire, monkeypatch):
    """If the shop already approved, don't re-ask — tell the model to place the order."""
    price_wire["approved"] = Decimal("3250")

    async def _get(shop_id, pid, client):
        return _product()

    monkeypatch.setattr(svc, "get_product", _get)
    res = await request_price(_shop_obj(), "p1", uuid4(), Decimal("3100"))
    assert res == {"status": "already_approved", "price_aed": "3250"}
    assert price_wire["opened"] is None and price_wire["notified"] is None


@pytest.mark.asyncio
async def test_request_price_refused_when_negotiation_off(price_wire, monkeypatch):
    price_wire["on"] = False

    async def _get(shop_id, pid, client):
        return _product()

    monkeypatch.setattr(svc, "get_product", _get)
    res = await request_price(_shop_obj(), "p1", uuid4(), Decimal("3100"))
    assert res == {"error": "negotiation_off"}
    assert price_wire["opened"] is None  # no request, shop never bothered — "when off, no discount"


@pytest.mark.asyncio
async def test_approve_price_at_requested_and_tells_customer(price_wire):
    price = await approve_price(_shop_obj(), 4)
    assert price == Decimal("3100")
    assert price_wire["status"] == ("approved", Decimal("3100"))
    ident, text = price_wire["customer"]
    assert ident == "p1" and "3100 AED" in text


@pytest.mark.asyncio
async def test_custom_price_counters_with_shop_price(price_wire):
    price = await approve_price(_shop_obj(), 4, Decimal("3250"))
    assert price == Decimal("3250")
    assert price_wire["status"] == ("approved", Decimal("3250"))
    assert "3250 AED" in price_wire["customer"][1]


@pytest.mark.asyncio
async def test_deny_price_tells_customer_the_list_price(price_wire, monkeypatch):
    async def _get(shop_id, pid, client):
        return _product(sell="3400")

    monkeypatch.setattr(svc, "get_product", _get)
    await deny_price(_shop_obj(), 4)
    assert price_wire["status"] == ("denied", None)
    assert "3400 AED" in price_wire["customer"][1]


@pytest.mark.asyncio
async def test_confirm_order_decrements_stock_and_notifies_customer(monkeypatch):
    cap = {"status": None, "customer": None}
    draft = {
        "id": "o1", "product_id": "pid", "quantity": 2, "phone": "p1", "address": "Marina",
        "selling_price": "6800", "discount_amount": "100", "delivery_date": None,
        "products": {"brand": "Samsung", "model": "S23", "color": "green"},
    }

    async def _get_draft(shop_id, num, client):
        return draft

    async def _dec(shop_id, pid, qty, client):
        return True  # stock available

    async def _set(oid, status, by, client):
        cap["status"] = status

    async def _to_customer(shop, identity, text):
        cap["customer"] = (identity, text)
        return True

    monkeypatch.setattr(svc, "_get_draft", _get_draft)
    monkeypatch.setattr(svc, "_decrement_stock", _dec)
    monkeypatch.setattr(svc, "_set_status", _set)
    monkeypatch.setattr(svc, "send_to_customer", _to_customer)

    await confirm_order(_shop_obj(), 7)
    assert cap["status"] == "confirmed"
    ident, text = cap["customer"]
    assert ident == "p1" and "#7 confirmed" in text and "6700 AED" in text  # net = 6800 - 100


@pytest.mark.asyncio
async def test_confirm_order_out_of_stock_raises_and_does_not_confirm(monkeypatch):
    cap = {"status": None, "customer": False}

    async def _get_draft(shop_id, num, client):
        return {"id": "o1", "product_id": "pid", "quantity": 2}

    async def _dec(shop_id, pid, qty, client):
        return False  # sold out between draft and confirm

    async def _set(*a):
        cap["status"] = "SHOULD-NOT-HAPPEN"

    async def _to_customer(*a):
        cap["customer"] = True
        return True

    monkeypatch.setattr(svc, "_get_draft", _get_draft)
    monkeypatch.setattr(svc, "_decrement_stock", _dec)
    monkeypatch.setattr(svc, "_set_status", _set)
    monkeypatch.setattr(svc, "send_to_customer", _to_customer)

    with pytest.raises(OutOfStock):
        await confirm_order(_shop_obj(), 7)
    assert cap["status"] is None and cap["customer"] is False  # nothing oversold, no false confirm


@pytest.mark.asyncio
async def test_reject_order_cancels_without_messaging_customer(monkeypatch):
    cap = {"status": None}

    async def _get_draft(shop_id, num, client):
        return {"id": "o1"}

    async def _set(oid, status, by, client):
        cap["status"] = status

    monkeypatch.setattr(svc, "_get_draft", _get_draft)
    monkeypatch.setattr(svc, "_set_status", _set)
    # send_to_customer must NOT be called on reject (design #2) — leave it real; if called it'd try network
    await reject_order(_shop_obj(), 7, "too far")
    assert cap["status"] == "cancelled"


# --- rider assignment (SPEC §10) ---
from app.orders.service import assign_delivery  # noqa: E402


def _order_row(status="confirmed"):
    return {
        "id": "o1", "status": status, "customer_name": "Ali", "phone": "p1", "address": "Marina",
        "quantity": 1, "delivery_date": None, "special_instructions": None,
        "selling_price": "3400", "discount_amount": "150",  # COD = net = 3250
        "products": {"brand": "Samsung", "model": "S23", "color": "green"},
    }


@pytest.mark.asyncio
async def test_assign_delivery_notifies_linked_rider_with_cod(monkeypatch):
    cap = {"set": None, "msg": None}

    async def _get_order(shop_id, num, client):
        return _order_row("confirmed")

    async def _get_rider(shop_id, rider_id, client=None):
        return {"id": str(rider_id), "name": "Sami", "telegram_id": 999}

    async def _balance(shop_id, rider_id, client=None):
        return Decimal("500")  # cash the rider already holds

    async def _set_rider(oid, rid, cod, client):
        cap["set"] = (oid, str(rid), cod)

    async def _send_to_rider(tid, text, reply_markup=None):
        cap["msg"] = (tid, text)
        return True

    monkeypatch.setattr(svc, "_get_order", _get_order)
    monkeypatch.setattr(svc, "_set_rider", _set_rider)
    monkeypatch.setattr("app.riders.service.get_rider", _get_rider)
    monkeypatch.setattr("app.riders.service.cod_balance", _balance)
    monkeypatch.setattr("app.telegram_bot.notify.send_to_rider", _send_to_rider)

    rid = uuid4()
    res = await assign_delivery(_shop_obj(), 8, rid)
    assert res["notified"] is True and res["cod"] == Decimal("3250")  # net = 3400 − 150
    assert cap["set"] == ("o1", str(rid), Decimal("3250"))
    tid, text = cap["msg"]
    assert tid == 999 and "#8" in text and "Marina" in text and "Samsung S23" in text
    assert "COD): 3250 AED" in text          # cash to collect on this order
    assert "already hold: 500 AED" in text   # running balance shown at assignment
    assert "/accept 8" in text and "/notreceived 8" in text  # custody handshake offered


@pytest.mark.asyncio
async def test_assign_delivery_unlinked_rider_assigns_but_flags(monkeypatch):
    async def _get_order(shop_id, num, client):
        return _order_row("packed")

    async def _get_rider(shop_id, rider_id, client=None):
        return {"id": str(rider_id), "name": "Sami", "telegram_id": None}  # onboarded, not linked

    async def _set_rider(oid, rid, cod, client):
        pass

    monkeypatch.setattr(svc, "_get_order", _get_order)
    monkeypatch.setattr(svc, "_set_rider", _set_rider)
    monkeypatch.setattr("app.riders.service.get_rider", _get_rider)

    res = await assign_delivery(_shop_obj(), 8, uuid4())
    assert res["notified"] is False  # assigned, but nothing pushed (rider hasn't linked Telegram)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["draft", "delivered", "cancelled"])
async def test_assign_delivery_rejects_non_assignable_status(monkeypatch, status):
    called = {"set": False}

    async def _get_order(shop_id, num, client):
        return _order_row(status)

    async def _set_rider(oid, rid, cod, client):
        called["set"] = True

    monkeypatch.setattr(svc, "_get_order", _get_order)
    monkeypatch.setattr(svc, "_set_rider", _set_rider)
    with pytest.raises(svc.InvalidTransition):
        await assign_delivery(_shop_obj(), 8, uuid4())
    assert called["set"] is False  # rejected before any write — never assigns to a done/draft order


# --- price requests: the live column is `phone`, not `identity` (regression: "Internal error") ---
@pytest.mark.asyncio
async def test_list_price_requests_selects_phone_column():
    """Guards the bug where the select named a nonexistent `identity` column → PostgREST error.
    Assert the query asks for `phone` and hands the renderer a `phone` field."""
    captured = {}

    class _SelSB:
        def table(self, n):
            captured["table"] = n
            return self

        def select(self, cols):
            captured["cols"] = cols
            return self

        def eq(self, *a):
            return self

        def order(self, *a):
            return self

        def execute(self):
            class _R:
                data = [{"request_number": 1, "phone": "501234567",
                         "requested_price": "3400",
                         "products": {"brand": "Samsung", "model": "S23", "selling_price": "3600"}}]
            return _R()

    rows = await svc.list_price_requests(uuid4(), client=_SelSB())
    assert "phone" in captured["cols"] and "identity" not in captured["cols"]
    assert rows[0]["phone"] == "501234567"  # renderer reads r['phone']


# --- product stats (Q-014): what sold, what didn't ---
from app.orders.service import _fold_product_stats, notify_low_stock  # noqa: E402


def _pstat_order(pid, sell, disc, cost, qty):
    return {"product_id": pid, "quantity": qty, "selling_price": str(sell),
            "discount_amount": str(disc), "products": {"cost_price": str(cost)}}


def test_fold_product_stats_sums_per_product_best_seller_first():
    products = [
        {"id": "p1", "product_number": 1, "brand": "Samsung", "model": "S23", "quantity": 4},
        {"id": "p2", "product_number": 2, "brand": "Apple", "model": "iPhone 15", "quantity": 1},
    ]
    orders = [
        _pstat_order("p1", 2000, 0, 1500, 1),
        _pstat_order("p1", 2000, 100, 1500, 1),   # same product, second sale
        _pstat_order("p2", 5000, 0, 4000, 1),
    ]
    rows = _fold_product_stats(orders, products)
    assert [r["code"] for r in rows] == ["PR0002", "PR0001"]  # 5000 revenue beats 4000
    s23 = next(r for r in rows if r["code"] == "PR0001")
    assert s23["sold_qty"] == 2 and s23["revenue"] == Decimal("4000")
    assert s23["profit"] == Decimal("900")  # (2000-1500) + (2000-100-1500)
    assert s23["stock"] == 4


def test_fold_product_stats_lists_unsold_products_with_zeros():
    # Dead stock is the whole point of the report — it must not vanish just because it never sold.
    products = [{"id": "p9", "product_number": 9, "brand": "Nokia", "model": "3310", "quantity": 7}]
    rows = _fold_product_stats([], products)
    assert len(rows) == 1
    assert rows[0] == {"code": "PR0009", "label": "Nokia 3310", "sold_qty": 0,
                       "revenue": Decimal("0"), "profit": Decimal("0"), "stock": 7}


def test_fold_product_stats_revenue_matches_profit_summary_definition():
    # /profit counts revenue as gross selling_price; product stats must agree or the keeper
    # sees two reports that contradict each other.
    products = [{"id": "p1", "product_number": 1, "brand": "B", "model": "M", "quantity": 0}]
    orders = [_pstat_order("p1", 1000, 250, 600, 1)]
    rows = _fold_product_stats(orders, products)
    assert rows[0]["revenue"] == Decimal("1000")           # gross, like _aggregate
    assert rows[0]["profit"] == Decimal("150")             # (1000-250) - 600


# --- low-stock alerts (migration 010) ---
class _LowStockShop:
    def __init__(self):
        self.id = uuid4()
        self.client_id = uuid4()
        self.name = "Shop 01"


def _lowstock_product(quantity, min_qty, number=1):
    from app.products.models import Product

    return Product(id=uuid4(), shop_id=uuid4(), category="Mobile", brand="Samsung", model="S23",
                   condition="New", cost_price=Decimal("1000"), selling_price=Decimal("1500"),
                   quantity=quantity, min_qty=min_qty, product_number=number)


@pytest.fixture
def low_stock_wire(monkeypatch):
    cap = {"shop_msg": None, "owner_msg": None, "product": None, "client": None}

    async def _get_product(shop_id, product_id, client=None):
        return cap["product"]

    async def _notify_shop(shop, text, reply_markup=None):
        cap["shop_msg"] = text

    async def _send_owner(tid, text, reply_markup=None):
        cap["owner_msg"] = (tid, text)
        return True

    class _Repo:
        async def get_client(self, cid):
            return cap["client"]

    monkeypatch.setattr("app.products.service.get_product", _get_product)
    monkeypatch.setattr(svc, "_notify_shop", _notify_shop)
    monkeypatch.setattr("app.telegram_bot.notify.send_to_shopowner", _send_owner)
    monkeypatch.setattr("app.db.factory.get_tenant_repo", lambda: _Repo())
    return cap


@pytest.mark.asyncio
async def test_notify_low_stock_alerts_shop_and_owner_at_threshold(low_stock_wire):
    low_stock_wire["product"] = _lowstock_product(quantity=2, min_qty=2)  # just hit the threshold
    low_stock_wire["client"] = type("C", (), {"telegram_id": 555})()

    assert await notify_low_stock(_LowStockShop(), uuid4()) is True
    assert "Low stock" in low_stock_wire["shop_msg"] and "PR0001" in low_stock_wire["shop_msg"]
    assert "2 left (alert at 2)" in low_stock_wire["shop_msg"]
    assert low_stock_wire["owner_msg"][0] == 555  # owner sees it too — that's the anti-blindness bit


@pytest.mark.asyncio
async def test_notify_low_stock_silent_when_threshold_is_off(low_stock_wire):
    low_stock_wire["product"] = _lowstock_product(quantity=0, min_qty=0)  # 0 = alerts off (the default)
    assert await notify_low_stock(_LowStockShop(), uuid4()) is False
    assert low_stock_wire["shop_msg"] is None


@pytest.mark.asyncio
async def test_notify_low_stock_silent_above_threshold(low_stock_wire):
    low_stock_wire["product"] = _lowstock_product(quantity=5, min_qty=2)
    assert await notify_low_stock(_LowStockShop(), uuid4()) is False
    assert low_stock_wire["shop_msg"] is None


@pytest.mark.asyncio
async def test_notify_low_stock_never_raises(monkeypatch):
    # An alert that blows up must not take the sale down with it.
    async def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.products.service.get_product", _boom)
    assert await notify_low_stock(_LowStockShop(), uuid4()) is False


@pytest.mark.asyncio
async def test_notify_low_stock_skips_owner_without_telegram(low_stock_wire):
    low_stock_wire["product"] = _lowstock_product(quantity=1, min_qty=3)
    low_stock_wire["client"] = type("C", (), {"telegram_id": None})()  # owner never linked
    assert await notify_low_stock(_LowStockShop(), uuid4()) is True
    assert low_stock_wire["shop_msg"] is not None  # shop still told
    assert low_stock_wire["owner_msg"] is None
