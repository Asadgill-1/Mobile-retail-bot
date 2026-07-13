"""AUDIT CATEGORY 3 — E-commerce concurrency: two buyers, one unit of stock.

Two shopkeeper confirmations for the same product run concurrently while stock_count == 1. The
decrement must go through the atomic Postgres RPC (`decrement_stock`, migration 003 — a conditional
`UPDATE … WHERE quantity >= n`), never a read-then-write on the raw table. The RPC only decrements
when stock is sufficient, so of two racing confirmations exactly one succeeds; the other gets a
falsy result and `confirm_order` raises OutOfStock (the shopkeeper is told, and can tell the buyer).

The fake Supabase client below emulates the RPC's atomic conditional decrement under a real
threading.Lock — `confirm_order` runs the RPC inside `asyncio.to_thread`, so two gathered
confirmations contend on genuine OS threads, exactly as two Postgres backends would on the row lock.

Pass: exactly one confirmation succeeds, one raises OutOfStock, final stock == 0 (never negative,
never split), and the decrement was done via `.rpc('decrement_stock', …)`, not a raw table update.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.orders.service as svc
from app.orders.service import OutOfStock, confirm_order
from app.tenants.models import Shop

PRODUCT_ID = "prod-x"


class _Exec:
    def __init__(self, client: "RaceClient", name: str, params: dict) -> None:
        self._client, self._name, self._params = client, name, params

    def execute(self):
        if self._name != "decrement_stock":
            return SimpleNamespace(data=[])
        p = self._params
        # Atomic check-and-decrement — the row lock a real Postgres SELECT FOR UPDATE / conditional
        # UPDATE gives us. Returns a row only when there was enough stock (truthy = success).
        with self._client.lock:
            have = self._client.stock.get(p["p_id"], 0)
            if have >= p["n"]:
                self._client.stock[p["p_id"]] = have - p["n"]
                return SimpleNamespace(data=[{"quantity": self._client.stock[p["p_id"]]}])
            return SimpleNamespace(data=[])  # not enough stock → conditional UPDATE hit 0 rows


class _Table:
    """Records any raw table access so the test can prove stock never went through one."""
    def __init__(self, client: "RaceClient", name: str) -> None:
        self._client, self._name = client, name

    def update(self, *a, **k):
        self._client.table_updates.append(self._name)
        return self

    def eq(self, *a, **k):
        return self

    def execute(self):
        return SimpleNamespace(data=[])


class RaceClient:
    def __init__(self, stock: int) -> None:
        self.stock = {PRODUCT_ID: stock}
        self.lock = threading.Lock()
        self.rpc_calls: list[str] = []
        self.table_updates: list[str] = []

    def rpc(self, name: str, params: dict) -> _Exec:
        self.rpc_calls.append(name)
        return _Exec(self, name, params)

    def table(self, name: str) -> _Table:
        return _Table(self, name)


@pytest.fixture
def confirm_env(monkeypatch):
    """Stub everything confirm_order touches except the real atomic-decrement path."""
    draft = {
        "id": "order-1", "product_id": PRODUCT_ID, "quantity": 1, "status": "draft",
        "selling_price": "999", "discount_amount": "0", "address": "12 St", "phone": "cust",
        "delivery_date": None, "products": {"brand": "Redmi", "model": "Note 13"},
    }

    async def _get_draft(shop_id, order_number, client):
        return dict(draft)

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(svc, "_get_draft", _get_draft)
    monkeypatch.setattr(svc, "_set_status", _noop)
    monkeypatch.setattr(svc, "send_to_customer", _noop)
    monkeypatch.setattr(svc, "_remember_to_customer", _noop)
    return SimpleNamespace(shop=Shop(id=uuid4(), client_id=uuid4(), name="Shop"))


@pytest.mark.asyncio
async def test_concurrent_purchase_one_wins_zero_oversell(confirm_env):
    client = RaceClient(stock=1)

    async def _confirm(n: int) -> str:
        try:
            await confirm_order(confirm_env.shop, n, client)
            return "ok"
        except OutOfStock:
            return "out_of_stock"

    r1, r2 = await asyncio.gather(_confirm(101), _confirm(102))

    assert sorted([r1, r2]) == ["ok", "out_of_stock"]   # exactly one success
    assert client.stock[PRODUCT_ID] == 0                # never negative, never split
    assert "decrement_stock" in client.rpc_calls        # went through the atomic RPC…
    assert client.table_updates == []                   # …not a raw table read-then-write


@pytest.mark.asyncio
async def test_sequential_second_buyer_is_out_of_stock(confirm_env):
    client = RaceClient(stock=1)
    await confirm_order(confirm_env.shop, 201, client)          # first buyer takes the unit
    with pytest.raises(OutOfStock):
        await confirm_order(confirm_env.shop, 202, client)      # second is cleanly rejected
    assert client.stock[PRODUCT_ID] == 0
