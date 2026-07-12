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
    cod_trail,
    custody_transition,
    deliver_order,
    deliverable,
    parse_cash,
    reconcile_cod,
    report_window,
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
    """Records updates + inserts; enough for deliver/cancel/reconcile paths."""

    def __init__(self):
        self.updates = []   # (table, patch)
        self.inserts = []   # (table, row)
        self._t = None
        self._patch = None
        self._insert = None

    def table(self, name):
        self._t = name
        self._patch = self._insert = None
        return self

    def update(self, patch):
        self._patch = patch
        return self

    def insert(self, row):
        self._insert = row
        return self

    def eq(self, *a):
        return self

    def execute(self):
        if self._patch is not None:
            self.updates.append((self._t, self._patch))
        if self._insert is not None:
            self.inserts.append((self._t, self._insert))

        class _R:
            data = [{}]

        return _R()


def _my_order(status="shipped", custody="accepted"):
    return {
        "id": "o1", "shop_id": str(uuid4()), "rider_id": "r1", "order_number": 8,
        "status": status, "custody": custody, "phone": "p1", "address": "Marina",
        "quantity": 2, "product_id": "pid", "cod_amount": "3250",
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

    async def _dec(shop_id, pid, n, client):
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
