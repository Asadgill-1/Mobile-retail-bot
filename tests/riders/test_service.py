"""Riders service (SPEC §10): phone normalization + Telegram linking + tenant guard. Supabase faked."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.riders.service import RiderNotFound, _normalize_phone, get_rider, link_telegram


def test_normalize_phone_uae_forms_converge():
    # The owner may type a local 0-number; Telegram hands us a +971 contact — both must match.
    assert _normalize_phone("0501234567") == "501234567"
    assert _normalize_phone("+971501234567") == "501234567"
    assert _normalize_phone("971 50 123 4567") == "501234567"
    assert _normalize_phone("") == ""


class _FakeSB:
    """Minimal supabase stand-in: one delivery_persons table, records updates."""

    def __init__(self, rows):
        self._rows = rows
        self.updated = []  # (id, patch)
        self._t = None
        self._patch = None
        self._filters = []

    def table(self, name):
        self._t = name
        self._patch = None
        self._filters = []
        return self

    def select(self, *a):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def limit(self, *a):
        return self

    def update(self, patch):
        self._patch = patch
        return self

    def execute(self):
        rows = self._rows
        for col, val in self._filters:
            rows = [r for r in rows if str(r.get(col)) == str(val)]
        if self._patch is not None:  # an update: apply to the single filtered row
            for r in rows:
                r.update(self._patch)
                self.updated.append((r["id"], dict(self._patch)))
        class _R:
            data = rows
        return _R()


@pytest.mark.asyncio
async def test_link_telegram_links_every_matching_shop_row():
    """A person who rides for two shops has two rows with the same phone — both get linked."""
    rows = [
        {"id": "r1", "shop_id": "s1", "phone": "0501234567", "telegram_id": None},
        {"id": "r2", "shop_id": "s2", "phone": "+971501234567", "telegram_id": None},
        {"id": "r3", "shop_id": "s1", "phone": "0507654321", "telegram_id": None},  # different number
    ]
    sb = _FakeSB(rows)
    linked = await link_telegram("971501234567", 4242, client=sb)
    assert {r["id"] for r in linked} == {"r1", "r2"}  # r3 untouched
    assert all(r["telegram_id"] == 4242 for r in linked)


@pytest.mark.asyncio
async def test_link_telegram_no_match_returns_empty():
    sb = _FakeSB([{"id": "r1", "shop_id": "s1", "phone": "0501111111", "telegram_id": None}])
    assert await link_telegram("+971509999999", 1, client=sb) == []
    assert sb.updated == []  # nobody linked


@pytest.mark.asyncio
async def test_get_rider_tenant_guard_raises_for_other_shop():
    # Row belongs to shop s1; asking as a different shop id filters it out → RiderNotFound.
    sb = _FakeSB([{"id": "r1", "shop_id": "s1", "phone": "0501234567", "telegram_id": None}])
    with pytest.raises(RiderNotFound):
        await get_rider(uuid4(), uuid4(), client=sb)


# --- custody handshake (audit: who has the product) ---
import app.riders.service as rsvc  # noqa: E402
from datetime import datetime, timezone as _tz  # noqa: E402
from decimal import Decimal  # noqa: E402

from app.riders.service import (  # noqa: E402
    NotYourDelivery,
    cancel_delivery,
    cod_balance,
    cod_trail,
    custody_transition,
    deliver_order,
    deliverable,
    parse_cash,
    reconcile_cod,
    report_window,
    set_custody,
)


@pytest.mark.parametrize(
    "current,accept,expected",
    [
        ("offered", True, "accepted"),
        ("offered", False, "disputed"),
        ("none", True, "accepted"),    # legacy orders assigned before the feature
        ("none", False, "disputed"),
    ],
)
def test_custody_transition_from_pending(current, accept, expected):
    assert custody_transition(current, accept) == expected


@pytest.mark.parametrize("decided", ["accepted", "disputed"])
def test_custody_answer_is_written_once(decided):
    # The audit answer can't be flipped later — that's the whole point of the handshake.
    with pytest.raises(ValueError):
        custody_transition(decided, True)
    with pytest.raises(ValueError):
        custody_transition(decided, False)


@pytest.mark.parametrize(
    "status,custody,ok",
    [
        ("confirmed", "accepted", True),
        ("packed", "accepted", True),
        ("shipped", "accepted", True),
        ("shipped", "offered", False),    # must /accept first
        ("shipped", "none", False),
        ("shipped", "disputed", False),   # disputed order can't be delivered
        ("delivered", "accepted", False),  # already done
        ("cancelled", "accepted", False),
        ("draft", "accepted", False),
    ],
)
def test_deliverable_requires_active_status_and_accepted_custody(status, custody, ok):
    assert (deliverable(status, custody) is None) is ok


# --- cash + report windows (pure) ---
def test_parse_cash_accepts_amounts_rejects_junk():
    assert parse_cash("3,400") == Decimal("3400")
    assert parse_cash(" 0 ") == Decimal("0")
    assert parse_cash("3400 AED") == Decimal("3400")
    with pytest.raises(ValueError):
        parse_cash("-5")
    with pytest.raises(ValueError):
        parse_cash("tomorrow")


def test_report_window_default_one_arg_and_range():
    from datetime import date

    today = date(2026, 7, 12)
    s, e, label = report_window([], today)
    assert (s.date(), e.date()) == (date(2026, 7, 12), date(2026, 7, 13)) and "Today" in label
    s, e, _ = report_window(["yesterday"], today)
    assert (s.date(), e.date()) == (date(2026, 7, 11), date(2026, 7, 12))
    s, e, label = report_window(["2026-07-01", "2026-07-10"], today)
    assert (s.date(), e.date()) == (date(2026, 7, 1), date(2026, 7, 11))  # inclusive range
    s2, e2, _ = report_window(["2026-07-10", "2026-07-01"], today)  # swapped → same window
    assert (s2, e2) == (s, e)


# --- COD ledger math (pure) ---
def test_cod_trail_previous_today_and_balance():
    today_start = datetime(2026, 7, 12, 0, 0, tzinfo=_tz.utc)
    rows = [
        {"entry": "collect", "amount": "500", "created_at": "2026-07-11T10:00:00+00:00"},
        {"entry": "handover", "amount": "300", "created_at": "2026-07-11T18:00:00+00:00"},
        {"entry": "collect", "amount": "3400", "created_at": "2026-07-12T09:00:00+00:00"},
        {"entry": "handover", "amount": "1000", "created_at": "2026-07-12T10:00:00+00:00"},
    ]
    t = cod_trail(rows, today_start)
    assert t["previous"] == Decimal("200")        # 500 − 300 before today
    assert t["today_collect"] == Decimal("3400")
    assert t["today_handover"] == Decimal("1000")
    assert t["balance"] == Decimal("2600")        # 200 + 3400 − 1000


# --- service flows (DB/notify edges faked) ---
class _Shop:
    def __init__(self):
        self.id = uuid4()
        self.name = "Shop 01"


class _WriteSB:
    """Records updates + inserts; enough for deliver/cancel/reconcile paths.

    Optionally stateful: pass `order_row` and real writes land in that dict, so the lifecycle test
    can walk assign → accept → deliver with each step reading the state the previous one left.
    `cod_ledger` inserts accumulate and are served back on select, so the real `cod_balance` /
    `cod_trail` fold real rows — that fold IS the assertion, so it must not be stubbed. `keeps`
    answers the shops lookup behind `_rider_keeps_delivery` (migration 023). Filters are ignored;
    these flows only ever address one order and one rider.
    """

    def __init__(self, order_row=None, keeps=False, ledger=None):
        self.updates = []   # (table, patch)
        self.inserts = []   # (table, row)
        self.tables = []    # every table touched, in call order
        self.order_row = order_row
        self.keeps = keeps
        self.ledger = list(ledger or [])
        self._t = None
        self._patch = None
        self._insert = None
        self._select = False

    def table(self, name):
        self._t = name
        self.tables.append(name)
        self._patch = self._insert = None
        self._select = False
        return self

    def select(self, *a):
        self._select = True
        return self

    def update(self, patch):
        self._patch = patch
        return self

    def insert(self, row):
        self._insert = row
        return self

    def eq(self, *a):
        return self

    def limit(self, *a):
        return self

    def order(self, *a):
        return self

    def execute(self):
        rows = [{}]
        if self._patch is not None:
            self.updates.append((self._t, self._patch))
            if self._t == "orders" and self.order_row is not None:
                self.order_row.update(self._patch)  # next step reads what this one wrote
        if self._insert is not None:
            self.inserts.append((self._t, self._insert))
            if self._t == "cod_ledger":
                # created_at is a DB default; cod_trail reads it unconditionally, so stamp it here.
                self.ledger.append({"created_at": datetime.now(_tz.utc).isoformat(), **self._insert})
        if self._select:
            rows = {
                "shops": [{"rider_keeps_delivery": self.keeps}],
                "cod_ledger": self.ledger,
            }.get(self._t, [])

        class _R:
            data = rows

        return _R()


def _my_order(status="shipped", custody="accepted", fee="0"):
    return {
        "id": "o1", "shop_id": str(uuid4()), "rider_id": "r1", "order_number": 8,
        "status": status, "custody": custody, "phone": "p1", "address": "Marina",
        "quantity": 2, "product_id": "pid", "cod_amount": "3250", "delivery_fee": fee,
        "products": {"brand": "Samsung", "model": "S23"},
    }


@pytest.fixture
def rider_wire(monkeypatch):
    """Fake every edge the rider flows touch; capture what they write/send."""
    cap = {"status": None, "shop_msg": None, "cust_msg": None, "restock": None, "rider_msg": None}
    shop = _Shop()

    async def _get(rider_ids, num, client):
        return cap["order"]

    async def _shop_of(order):
        return shop

    async def _notify_shop(s, text):
        cap["shop_msg"] = text

    async def _set_status(oid, status, by, client):
        cap["status"] = (status, by)

    async def _send_cust(s, phone, text):
        cap["cust_msg"] = (phone, text)
        return True

    async def _remember(s, phone, text):
        pass

    async def _dec(shop_id, pid, n, client, **_):
        cap["restock"] = n
        return True

    monkeypatch.setattr(rsvc, "_get_my_order", _get)
    monkeypatch.setattr(rsvc, "_shop_of_order", _shop_of)
    monkeypatch.setattr(rsvc, "_notify_shop", _notify_shop)
    monkeypatch.setattr(rsvc, "_set_status", _set_status)
    monkeypatch.setattr(rsvc, "_decrement_stock", _dec)
    monkeypatch.setattr("app.telegram_bot.notify.send_to_customer", _send_cust)
    monkeypatch.setattr("app.orders.service._remember_to_customer", _remember)
    cap["shop"] = shop
    return cap


@pytest.mark.asyncio
async def test_deliver_order_writes_time_cash_ledger_and_notifies(rider_wire):
    rider_wire["order"] = _my_order("shipped", "accepted")
    sb = _WriteSB()
    at = datetime(2026, 7, 12, 18, 30, tzinfo=_tz.utc)

    await deliver_order(["r1"], "Sami", 8, Decimal("3250"), at, client=sb)

    assert rider_wire["status"] == ("delivered", "rider")
    t, patch = sb.updates[0]
    assert t == "orders" and patch["cash_received"] == "3250" and patch["delivered_at"] == at.isoformat()
    lt, row = sb.inserts[0]
    assert lt == "cod_ledger" and row["entry"] == "collect" and row["amount"] == "3250"
    assert "delivered" in rider_wire["cust_msg"][1]
    assert "Sami" in rider_wire["shop_msg"] and "3250 AED" in rider_wire["shop_msg"]
    assert "shops" not in sb.tables  # a zero-fee order short-circuits the rider-keeps-fee lookup


@pytest.mark.asyncio
async def test_deliver_order_refused_without_accepted_custody(rider_wire):
    rider_wire["order"] = _my_order("shipped", "offered")  # never confirmed pickup
    with pytest.raises(ValueError):
        await deliver_order(["r1"], "Sami", 8, Decimal("100"), datetime.now(_tz.utc), client=_WriteSB())
    assert rider_wire["status"] is None  # nothing written


@pytest.mark.asyncio
async def test_cancel_delivery_restocks_and_records_remarks(rider_wire):
    rider_wire["order"] = _my_order("packed", "accepted")
    sb = _WriteSB()

    await cancel_delivery(["r1"], "Sami", 8, "customer not answering", client=sb)

    assert rider_wire["status"] == ("cancelled", "rider")
    assert sb.updates[0][1] == {"cancel_remarks": "customer not answering"}
    assert rider_wire["restock"] == -2  # negative decrement = +2 back in stock
    assert "customer not answering" in rider_wire["shop_msg"]
    assert "couldn't be completed" in rider_wire["cust_msg"][1]


@pytest.mark.asyncio
async def test_reconcile_cod_trail_math_and_rider_push(monkeypatch):
    shop = _Shop()
    rider = {"id": "r1", "name": "Sami", "telegram_id": 999}
    rows = [
        {"entry": "collect", "amount": "500", "created_at": "2026-07-01T10:00:00+00:00"},
        {"entry": "handover", "amount": "300", "created_at": "2026-07-01T18:00:00+00:00"},
        {"entry": "collect", "amount": "3400", "created_at": datetime.now(_tz.utc).isoformat()},
    ]
    pushed = {}

    async def _rows(shop_id, rider_id, client=None):
        return rows

    async def _push(tid, text):
        pushed["msg"] = (tid, text)
        return True

    monkeypatch.setattr(rsvc, "cod_rows", _rows)
    monkeypatch.setattr("app.telegram_bot.notify.send_to_rider", _push)
    sb = _WriteSB()

    trail = await reconcile_cod(shop, rider, Decimal("3000"), client=sb)

    # previous 200 + today 3400 − handover 3000 = 600 remaining
    assert trail["previous"] == Decimal("200") and trail["remaining"] == Decimal("600")
    lt, row = sb.inserts[0]
    assert lt == "cod_ledger" and row["entry"] == "handover" and row["amount"] == "3000"
    tid, text = pushed["msg"]
    assert tid == 999 and "600 AED" in text and "3000 AED" in text  # rider gets the same trail


# --- who ends up with the delivery fee (migration 023) ---
@pytest.mark.asyncio
async def test_deliver_order_rider_keeps_the_delivery_fee(rider_wire):
    """The rider collects product+delivery from the customer but only OWES the shop the product
    portion, so the 'collect' claim — what they hold on the shop's behalf — is cash minus the fee."""
    rider_wire["order"] = _my_order("shipped", "accepted", fee="150")
    sb = _WriteSB(keeps=True)

    await deliver_order(["r1"], "Sami", 8, Decimal("3570.00"), datetime.now(_tz.utc), client=sb)

    lt, row = sb.inserts[0]
    assert lt == "cod_ledger" and Decimal(row["amount"]) == Decimal("3420.00")
    assert "rider kept 150 AED delivery" in row["note"]
    assert sb.updates[0][1]["cash_received"] == "3570.00"  # the order still records the full cash
    assert "Rider keeps 150 AED delivery — owes shop 3420.00 AED" in rider_wire["shop_msg"]


@pytest.mark.asyncio
async def test_deliver_order_fee_goes_to_the_shop_when_the_flag_is_off(rider_wire):
    """Same order, flag off: the rider owes the whole collection, delivery included. Every shop in
    the live DB is on this setting, so it is the path that actually runs."""
    rider_wire["order"] = _my_order("shipped", "accepted", fee="150")
    sb = _WriteSB(keeps=False)

    await deliver_order(["r1"], "Sami", 8, Decimal("3570.00"), datetime.now(_tz.utc), client=sb)

    row = sb.inserts[0][1]
    assert Decimal(row["amount"]) == Decimal("3570.00") and "rider kept" not in row["note"]
    assert "Rider keeps" not in rider_wire["shop_msg"]
    assert "shops" in sb.tables  # it read the flag rather than short-circuiting on a zero fee


@pytest.mark.asyncio
async def test_deliver_order_short_collection_never_writes_a_negative_claim(rider_wire):
    """A rider keeping a 150 fee who collects only 100 owes nothing — the claim clamps at 0 instead
    of going negative and inverting the ledger. The 50 shortfall stays visible in the cash line."""
    rider_wire["order"] = _my_order("shipped", "accepted", fee="150")
    sb = _WriteSB(keeps=True)

    await deliver_order(["r1"], "Sami", 8, Decimal("100"), datetime.now(_tz.utc), client=sb)

    assert Decimal(sb.inserts[0][1]["amount"]) == Decimal("0")
    assert "Cash received: 100 AED" in rider_wire["shop_msg"]


# --- custody handshake, at the DB (the pure rule is covered above) ---
@pytest.mark.asyncio
async def test_set_custody_accept_writes_the_answer_and_tells_the_shop(rider_wire):
    rider_wire["order"] = _my_order("confirmed", "offered")
    sb = _WriteSB()

    order = await set_custody(["r1"], "Sami", 8, True, client=sb)

    t, patch = sb.updates[0]
    assert t == "orders" and patch["custody"] == "accepted"
    assert datetime.fromisoformat(patch["custody_at"])  # a real stamp, not a placeholder
    assert "Pickup confirmed" in rider_wire["shop_msg"] and "Sami" in rider_wire["shop_msg"]
    assert order["custody"] == "accepted"  # the returned row is mutated to the new state


@pytest.mark.asyncio
async def test_set_custody_dispute_alerts_the_shop(rider_wire):
    """The rider says the product was never handed to them. The shop has to hear that immediately —
    the package is unaccounted for while this is open."""
    rider_wire["order"] = _my_order("confirmed", "offered")
    sb = _WriteSB()

    await set_custody(["r1"], "Sami", 8, False, client=sb)

    assert sb.updates[0][1]["custody"] == "disputed"
    assert "🚨 PICKUP DISPUTED" in rider_wire["shop_msg"] and "NOT handed" in rider_wire["shop_msg"]


@pytest.mark.asyncio
@pytest.mark.parametrize("answered", ["accepted", "disputed"])
async def test_set_custody_cannot_be_re_decided_and_writes_nothing(rider_wire, answered):
    """The audit answer is written once: a rider can't accept a package and later claim they never
    got it. Refusing after the write would defeat the whole handshake, so assert nothing moved."""
    rider_wire["order"] = _my_order("confirmed", answered)
    sb = _WriteSB()

    with pytest.raises(ValueError):
        await set_custody(["r1"], "Sami", 8, True, client=sb)

    assert sb.updates == [] and rider_wire["shop_msg"] is None


@pytest.mark.asyncio
async def test_cod_balance_folds_collects_minus_handovers():
    """What a rider owes is derived from the ledger on every read, never stored in a column — so
    this fold is the only definition of it that exists."""
    sb = _WriteSB(ledger=[
        {"entry": "collect", "amount": "500", "created_at": "2026-07-01T10:00:00+00:00"},
        {"entry": "collect", "amount": "3400", "created_at": "2026-07-02T10:00:00+00:00"},
        {"entry": "handover", "amount": "3000", "created_at": "2026-07-02T18:00:00+00:00"},
    ])

    assert await cod_balance(uuid4(), "r1", client=sb) == Decimal("900")


# --- the whole arc: assign → accept → deliver → reconcile ---
from app.orders.service import assign_delivery  # noqa: E402


@pytest.mark.asyncio
@pytest.mark.parametrize("keeps", [False, True])
async def test_the_delivery_arc_closes_the_money_loop(monkeypatch, rider_wire, keeps):
    """One order walked end to end across both service modules, asserting the cash reconciles to
    exactly zero.

    COD is fixed once at assignment (`with_vat(net + fee)`), collected by the rider, claimed on the
    ledger minus whatever fee they keep, then handed over. Three separate computations in two
    modules have to agree: if VAT gets applied twice, the fee double-counted, or the discount lost,
    the balance won't land on 0 and the shop is silently short by the drift. That identity — not any
    single step — is what this test exists to pin.
    """
    shop = rider_wire["shop"]
    order = _my_order("confirmed", "none", fee="150")
    order["selling_price"], order["discount_amount"] = "3400", "150"  # net 3250, plus the 150 fee
    order["shop_id"] = str(shop.id)
    rider_wire["order"] = order
    sb = _WriteSB(order_row=order, keeps=keeps)
    rid = uuid4()

    async def _get_order(shop_id, num, client):
        return order

    async def _get_rider(shop_id, rider_id, client=None):
        return {"id": str(rider_id), "name": "Sami", "telegram_id": None}  # nothing to push

    async def _set_status(oid, status, by, client):
        order["status"] = status  # so `deliverable` reads what the previous step actually wrote

    monkeypatch.setattr("app.orders.service._get_order", _get_order)
    monkeypatch.setattr("app.riders.service.get_rider", _get_rider)
    monkeypatch.setattr(rsvc, "_set_status", _set_status)

    # 1. assign — COD carries the VAT the stored figures don't, and the status must NOT move.
    res = await assign_delivery(shop, 8, rid, client=sb)
    assert res["cod"] == Decimal("3570.00")  # (3400 − 150) + 150 fee, +5%
    assert order["custody"] == "offered" and order["status"] == "confirmed"

    # 2. the custody gate holds — no delivery before pickup is confirmed, and nothing is written.
    with pytest.raises(ValueError):
        await deliver_order([str(rid)], "Sami", 8, res["cod"], datetime.now(_tz.utc), client=sb)
    assert sb.ledger == []

    # 3. accept, then deliver the exact COD that was quoted at assignment.
    await set_custody([str(rid)], "Sami", 8, True, client=sb)
    assert order["custody"] == "accepted"
    await deliver_order([str(rid)], "Sami", 8, res["cod"], datetime.now(_tz.utc), client=sb)
    assert order["status"] == "delivered"

    # 4. what the rider owes the shop = COD minus the fee they keep.
    owed = await cod_balance(shop.id, str(rid), client=sb)
    assert owed == (Decimal("3420.00") if keeps else Decimal("3570.00"))

    # 5. hand it over — the loop closes at zero, and the rider's pocket is exactly the fee.
    trail = await reconcile_cod(shop, {"id": str(rid), "name": "Sami"}, owed, client=sb)
    assert trail["remaining"] == Decimal("0")
    assert await cod_balance(shop.id, str(rid), client=sb) == Decimal("0")
    assert res["cod"] - owed == (Decimal("150") if keeps else Decimal("0"))
