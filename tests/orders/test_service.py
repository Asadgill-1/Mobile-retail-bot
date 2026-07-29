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


def test_aggregate_counts_delivery_the_shop_keeps_as_revenue():
    """023 added delivery_fee and said it flows to shop revenue unless the rider keeps it. This
    aggregate predated the column, so /profit under-reported by exactly the fees collected while
    both dashboards counted them — the same shop read two different revenues."""
    row = _row(1000, 0, 600, 1)
    row["delivery_fee"] = "25"

    assert _aggregate([row]).revenue == Decimal("1025")
    assert _aggregate([row], keeps_delivery=True).revenue == Decimal("1000")


def test_aggregate_revenue_is_gross_with_discounts_reported_apart():
    """Revenue is gross sales; net is revenue − discounts. Both dashboards copy this, so changing
    one side silently would make the console and the bot disagree."""
    s = _aggregate([_row(1500, 50, 600, 1)])

    assert s.revenue == Decimal("1500")
    assert s.discounts == Decimal("50")
    assert s.profit == Decimal("850")  # (1500 − 50) − 600


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
        """Table-aware: profit_summary now also reads counter_sales (walk-in takings)."""
        def table(self, n):
            self._t = n
            return self
        def select(self, *a): return self
        def eq(self, *a): return self
        def gte(self, *a): return self
        def lt(self, *a): return self
        def neq(self, *a): return self
        def order(self, *a): return self
        def execute(self):
            if self._t == "counter_sales":
                rows = []
            elif self._t == "shops":
                # 023: the shop keeps the delivery cash, so any fee counts as its revenue.
                rows = [{"rider_keeps_delivery": False}]
            else:
                rows = [_row(2499, 0, 2000, 1)]

            class _R:
                data = rows
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
           "approved": None, "pending": None, "remembered": None,
           # migration 027: ask-every-time is the default, so these tests keep the old behaviour
           # unless a test opts into AI authority.
           "ask_every_time": True, "max_pct": Decimal("0"), "granted": None,
           "sweetener": None, "granted_ask": None}

    async def _settings(shop_id, client):
        return cap["on"], cap["ask_every_time"], cap["max_pct"]

    async def _sweetener(shop_id, product_id, client):
        return cap["sweetener"]

    async def _grant(shop, identity, product, price, client, *, asked=None):
        cap["granted"] = price
        cap["granted_ask"] = asked

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
    monkeypatch.setattr(svc, "_haggle_settings", _settings)
    monkeypatch.setattr(svc, "_grant_price", _grant)
    monkeypatch.setattr(svc, "_haggle_offer", _sweetener)
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
    """A re-ask the approval already beats must not re-open anything — just go book it.

    This is the anti-flood guard: the model re-asked and made requests #3/#4 in testing.
    """
    price_wire["approved"] = Decimal("3250")

    async def _get(shop_id, pid, client):
        return _product()

    monkeypatch.setattr(svc, "get_product", _get)
    res = await request_price(_shop_obj(), "p1", uuid4(), Decimal("3300"))
    assert res == {"status": "already_approved", "price_aed": "3412.50"}  # 3250 net + 5% VAT
    assert price_wire["opened"] is None and price_wire["notified"] is None


@pytest.mark.asyncio
async def test_pushing_below_an_approved_price_asks_the_shop(price_wire, monkeypatch):
    """Approved at 3250, now they want 3100 — that is a new concession, so a human decides.

    The dedup guard still bounds it: a second push opens no further row (see the pending test).
    """
    price_wire["approved"] = Decimal("3250")

    async def _get(shop_id, pid, client):
        return _product()

    monkeypatch.setattr(svc, "get_product", _get)
    res = await request_price(_shop_obj(), "p1", uuid4(), Decimal("3100"))
    assert res == {"status": "asked_shop"}
    assert price_wire["opened"] == Decimal("3100")


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
    assert ident == "p1" and "3255 AED" in text  # 3100 approved net, quoted with VAT


@pytest.mark.asyncio
async def test_custom_price_counters_with_shop_price(price_wire):
    price = await approve_price(_shop_obj(), 4, Decimal("3250"))
    assert price == Decimal("3250")
    assert price_wire["status"] == ("approved", Decimal("3250"))
    assert "3412.50 AED" in price_wire["customer"][1]  # keeper counters net, customer hears gross


@pytest.mark.asyncio
async def test_deny_price_tells_customer_the_list_price(price_wire, monkeypatch):
    async def _get(shop_id, pid, client):
        return _product(sell="3400")

    monkeypatch.setattr(svc, "get_product", _get)
    await deny_price(_shop_obj(), 4)
    assert price_wire["status"] == ("denied", None)
    assert "3570 AED" in price_wire["customer"][1]  # list 3400 net + 5% VAT


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

    async def _no_offer(shop_id, d, fee, client):  # no active offer in this test
        from decimal import Decimal
        return {"discount": Decimal(str(d["discount_amount"])), "fee": fee,
                "free_delivery": False, "gift_text": None, "snapshot": None}

    async def _update(oid, patch, client):  # DB persistence stubbed out
        cap["patch"] = patch

    monkeypatch.setattr(svc, "_get_draft", _get_draft)
    monkeypatch.setattr(svc, "_decrement_stock", _dec)
    monkeypatch.setattr(svc, "_set_status", _set)
    monkeypatch.setattr(svc, "_apply_offer_at_confirm", _no_offer)
    monkeypatch.setattr(svc, "_update_order", _update)
    monkeypatch.setattr(svc, "send_to_customer", _to_customer)

    await confirm_order(_shop_obj(), 7)
    assert cap["status"] == "confirmed"
    ident, text = cap["customer"]
    assert ident == "p1" and "#7 confirmed" in text and "7035 AED" in text  # (6800-100) + 5% VAT


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
    assert res["notified"] is True and res["cod"] == Decimal("3412.50")  # (3400 − 150) + 5% VAT
    assert cap["set"] == ("o1", str(rid), Decimal("3412.50"))
    tid, text = cap["msg"]
    assert tid == 999 and "#8" in text and "Marina" in text and "Samsung S23" in text
    assert "COD): 3412.50 AED" in text       # real cash to collect — carries the VAT
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


# --- offer discount math (023) ---
def test_offer_discount_covers_every_type():
    from decimal import Decimal

    from app.orders.service import _offer_discount

    unit = Decimal("100")
    # percent: 10% of 100*2 = 20
    assert _offer_discount({"type": "percent_off", "value": 10}, unit, 2) == Decimal("20")
    # amount: flat 30, capped at the line total
    assert _offer_discount({"type": "amount_off", "value": 30}, unit, 1) == Decimal("30")
    assert _offer_discount({"type": "amount_off", "value": 500}, unit, 1) == Decimal("100")  # capped
    # bogo buy-1-get-1: qty 2 → 1 free unit = 100; qty 3 → still 1 free (one full group)
    assert _offer_discount({"type": "bogo", "value": 1}, unit, 2) == Decimal("100")
    assert _offer_discount({"type": "bogo", "value": 1}, unit, 3) == Decimal("100")
    # bulk: 10% off when qty ≥ min
    assert _offer_discount({"type": "bulk", "value": 3}, unit, 3) == Decimal("30")
    assert _offer_discount({"type": "bulk", "value": 3}, unit, 2) == Decimal("0")
    # non-discount types produce no line discount here
    assert _offer_discount({"type": "free_gift", "value": None}, unit, 1) == Decimal("0")
    assert _offer_discount({"type": "free_delivery", "value": None}, unit, 1) == Decimal("0")


# --- AI bargaining authority (migration 027) ---
def _floor_product(sell="3400", cost="2800", floor=None):
    p = _product(sell=sell, cost=cost)
    p.min_price = Decimal(floor) if floor is not None else None
    return p


@pytest.mark.parametrize(
    "sell,cost,min_price,pct,expected,why",
    [
        ("3400", "2800", None, "0",  "3400", "no authority granted -> the floor IS the list price"),
        ("3400", "2800", None, "5",  "3230", "5% of 3400 = 170 off"),
        ("3400", "2800", None, "50", "2800", "50% would break cost -> clamped at cost"),
        ("3400", "2800", "3100", "5", "3100", "explicit product floor wins over the 5% default"),
        ("3400", "2800", "3350", "20", "3350", "explicit floor is TIGHTER than 20% -> it still wins"),
        ("3400", "2800", "2000", "5", "2800", "a floor under cost is clamped up to cost"),
        ("1000", "900", None, "3",  "970", "rounds UP (970.0 exact) — never below the configured floor"),
        ("999",  "500", None, "10", "900", "899.1 rounds UP to 900, the protective direction"),
    ],
)
def test_bargain_floor(sell, cost, min_price, pct, expected, why):
    got = svc.bargain_floor(_floor_product(sell, cost, min_price), Decimal(pct))
    assert got == Decimal(expected), why
    assert got >= Decimal(cost), "cost clamp is absolute"


@pytest.mark.asyncio
async def test_ai_settles_at_the_asked_price_when_it_clears_the_floor(price_wire):
    """Authority granted, customer's offer is above the floor -> instant yes, no shopkeeper."""
    price_wire["ask_every_time"] = False
    price_wire["max_pct"] = Decimal("10")  # floor 3060

    res = await request_price(_shop_obj(), "p1", uuid4(), Decimal("3200"))

    assert res == {"status": "approved", "price_aed": "3360"}  # settled net 3200, quoted with VAT
    assert price_wire["granted"] == Decimal("3200")
    assert price_wire["opened"] is None, "the shopkeeper must not be asked"


@pytest.mark.asyncio
async def test_ai_counters_at_the_floor_on_a_lowball(price_wire):
    """Below the floor -> counter AT the floor, and record it so an accept books at that price."""
    price_wire["ask_every_time"] = False
    price_wire["max_pct"] = Decimal("10")  # 3400 -> floor 3060

    res = await request_price(_shop_obj(), "p1", uuid4(), Decimal("2000"))

    assert res == {"status": "counter", "price_aed": "3213"}  # floor 3060 net, quoted with VAT
    assert price_wire["granted"] == Decimal("3060")
    assert price_wire["opened"] is None


@pytest.mark.asyncio
async def test_pushing_below_a_granted_price_goes_to_the_shopkeeper(price_wire):
    """The AI spends its authority once. More than that is a human decision."""
    price_wire["ask_every_time"] = False
    price_wire["max_pct"] = Decimal("10")
    price_wire["approved"] = Decimal("3060")  # already settled there

    res = await request_price(_shop_obj(), "p1", uuid4(), Decimal("2500"))

    assert res == {"status": "asked_shop"}
    assert price_wire["opened"] == Decimal("2500")
    assert price_wire["granted"] is None, "authority already spent — must not grant again"


@pytest.mark.asyncio
async def test_asking_above_a_granted_price_just_books(price_wire):
    price_wire["ask_every_time"] = False
    price_wire["approved"] = Decimal("3060")

    res = await request_price(_shop_obj(), "p1", uuid4(), Decimal("3300"))

    assert res == {"status": "already_approved", "price_aed": "3213"}  # 3060 net + 5% VAT
    assert price_wire["opened"] is None


@pytest.mark.asyncio
async def test_ask_every_time_keeps_the_shopkeeper_in_the_loop(price_wire):
    """The default. Authority settings are irrelevant while this is on."""
    price_wire["ask_every_time"] = True
    price_wire["max_pct"] = Decimal("50")

    res = await request_price(_shop_obj(), "p1", uuid4(), Decimal("3000"))

    assert res == {"status": "asked_shop"}
    assert price_wire["granted"] is None


@pytest.mark.asyncio
async def test_negotiation_off_beats_every_authority_setting(price_wire):
    price_wire["on"] = False
    price_wire["ask_every_time"] = False
    price_wire["max_pct"] = Decimal("30")

    res = await request_price(_shop_obj(), "p1", uuid4(), Decimal("3000"))

    assert res == {"error": "negotiation_off"}
    assert price_wire["granted"] is None and price_wire["opened"] is None


@pytest.mark.asyncio
async def test_held_back_freebie_arrives_with_the_price_answer(price_wire):
    """The sweetener rides on request_price, not on the product listing.

    Handed to the model alongside the catalogue it spent the card in its opening line, before the
    customer had haggled at all — so the reveal is structural, not a prompt instruction.
    """
    price_wire["ask_every_time"] = False
    price_wire["max_pct"] = Decimal("10")
    price_wire["sweetener"] = "Free cover + screen protector"

    res = await request_price(_shop_obj(), "p1", uuid4(), Decimal("2000"))

    assert res["status"] == "counter"
    assert res["sweetener"] == "Free cover + screen protector"


@pytest.mark.asyncio
async def test_no_sweetener_key_when_the_shop_has_no_bargaining_offer(price_wire):
    price_wire["ask_every_time"] = False
    price_wire["sweetener"] = None

    res = await request_price(_shop_obj(), "p1", uuid4(), Decimal("3300"))

    assert "sweetener" not in res, "the model must never see an empty freebie to embellish"


@pytest.mark.asyncio
async def test_a_counter_records_what_the_customer_actually_asked(price_wire):
    """The shop's record of the negotiation should show the lowball AND what we settled at."""
    price_wire["ask_every_time"] = False
    price_wire["max_pct"] = Decimal("10")  # 3400 -> floor 3060

    await request_price(_shop_obj(), "p1", uuid4(), Decimal("2000"))

    assert price_wire["granted"] == Decimal("3060")      # settled at the floor
    assert price_wire["granted_ask"] == Decimal("2000")  # but they asked for 2000


@pytest.mark.asyncio
async def test_never_grants_above_the_list_price(price_wire):
    """A model asking MORE than list would otherwise write an above-list "approved" row, and
    draft_order turns that into a negative discount that `discount_amount >= 0` rejects — the
    customer would be promised a price and then have their order fail."""
    price_wire["ask_every_time"] = False
    price_wire["max_pct"] = Decimal("10")

    res = await request_price(_shop_obj(), "p1", uuid4(), Decimal("4000"))  # list is 3400

    assert price_wire["granted"] == Decimal("3400"), "clamped down to the list price"
    assert res["price_aed"] == "3570"  # clamped to list 3400 net, quoted with VAT
