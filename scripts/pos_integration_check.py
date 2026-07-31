#!/usr/bin/env python
"""Cross-channel POS integration check against a DISPOSABLE shop in the live project.

Proves the guarantees the reporting layer depends on but unit tests cannot reach: that stock cannot
be oversold when both channels sell the last unit at once, that invoice numbers are gap-free under
concurrency, that a void reverses inside its own payment bucket, and — the one the owner actually
asked about — that a shop's counter and online takings add up to the same revenue whether you ask
the Python (`/profit`) or the TypeScript (dashboard + console).

Everything it writes lives under one throwaway client/shop and is deleted in a `finally`, the same
discipline scripts/loadtest.py uses so a harness never pollutes real numbers.

    PYTHONPATH="src;config;." python scripts/pos_integration_check.py
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx

from app.core.config import settings

DUBAI = ZoneInfo("Asia/Dubai")
TAG = "postest"
# The dashboard repo — its aggregate() is the other half of the reconciliation.
DASHBOARD = Path(r"C:\Users\HPUSER\Desktop\mobile-shop-and-shop-owner-dashboard")

_BASE = settings.supabase_url.rstrip("/")
_H = {
    "apikey": settings.supabase_service_role_key,
    "Authorization": f"Bearer {settings.supabase_service_role_key}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

failures: list[str] = []


def check(name: str, got, want, note: str = "") -> None:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        got {got!r}, want {want!r}" + (f"  ({note})" if note else ""))
        failures.append(name)


async def rest(c: httpx.AsyncClient, method: str, path: str, **kw):
    r = await c.request(method, f"{_BASE}/rest/v1/{path}", headers=_H, timeout=30, **kw)
    r.raise_for_status()
    return r.json() if r.content else []


async def rpc(c: httpx.AsyncClient, fn: str, args: dict):
    r = await c.post(f"{_BASE}/rest/v1/rpc/{fn}", headers=_H, json=args, timeout=30)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------
async def check_stock_is_atomic(c, shop: str, product: str) -> None:
    """The last unit, sold by both channels at once. decrement_stock guards with `quantity >= n`
    inside one UPDATE, so exactly one caller may win — anything else is an oversell."""
    await rest(c, "PATCH", f"products?id=eq.{product}", json={"quantity": 1})

    wins = await asyncio.gather(
        *(rpc(c, "decrement_stock", {"p_id": product, "p_shop": shop, "n": 1}) for _ in range(8))
    )
    left = (await rest(c, "GET", f"products?id=eq.{product}&select=quantity"))[0]["quantity"]

    check("only one of 8 concurrent sells takes the last unit", sum(bool(w) for w in wins), 1)
    check("stock never goes negative", left, 0)


DOC = {"source": "counter", "items": [], "subtotal": "100.00", "vat_amount": "5.00",
       "total": "105.00", "created_by": TAG}


async def check_invoice_numbers_are_gap_free(c, shop: str) -> None:
    """FTA wants a per-business sequence and an auditor reads a gap as a suppressed sale. Numbers
    come from the BEFORE INSERT trigger (033), so they must survive concurrency AND a failed insert
    — the old two-round-trip RPC committed the counter first and burned one on every failure. Live
    proof it did: Shop 01 is missing invoice_number 3 and 4, and day_seq 1 of 2026-07-29."""
    async def last_no() -> int:
        rows = await rest(c, "GET", f"invoice_counters?shop_id=eq.{shop}&select=last_no")
        return rows[0]["last_no"] if rows else 0

    made = await asyncio.gather(
        *(rest(c, "POST", "invoices", json={"shop_id": shop, **DOC}) for _ in range(10))
    )
    nums = sorted(m[0]["invoice_number"] for m in made)
    check("10 concurrent inserts allocate unique numbers", len(set(nums)), 10)
    check("...and contiguous from 1", nums, list(range(1, 11)))

    # The burn test. A negative total with no credit_of violates 029's sign check, which fires
    # AFTER the trigger has already incremented — so only a shared transaction can undo it.
    before = await last_no()
    bad = await c.post(f"{_BASE}/rest/v1/invoices", headers=_H, timeout=30,
                       json={"shop_id": shop, **DOC,
                             "subtotal": "-1.00", "vat_amount": "-1.00", "total": "-1.00"})
    check("an invalid invoice is rejected", bad.status_code, 400)
    check("...and burns no invoice number", await last_no(), before)
    nxt = (await rest(c, "POST", "invoices", json={"shop_id": shop, **DOC}))[0]
    check("...so the next real one continues the sequence", nxt["invoice_number"], before + 1)


async def check_day_seq_per_kind(c, shop: str) -> None:
    """ODR-DD-MM-NNN and INV-DD-MM-NNN are independent per-day sequences (023). Orders still
    allocate through the RPC at insert (orders/service.py:130); invoices moved into the trigger,
    so this pins that neither side consumes the other's numbers."""
    day = datetime.now(DUBAI).date().isoformat()
    rows = await rest(
        c, "GET", f"daily_counters?shop_id=eq.{shop}&kind=eq.invoice&day=eq.{day}&select=last_no"
    )
    inv_before = rows[0]["last_no"] if rows else 0

    orders = await asyncio.gather(
        *(rpc(c, "next_day_seq", {"p_shop": shop, "p_kind": "order", "p_day": day}) for _ in range(5))
    )
    check("5 concurrent order refs are 1..5", sorted(orders), [1, 2, 3, 4, 5])

    inv = (await rest(c, "POST", "invoices", json={"shop_id": shop, **DOC}))[0]
    check("the invoice day sequence is separate, not advanced by orders",
          inv["day_seq"], inv_before + 1)


async def check_void_reverses_in_its_own_bucket(c, shop: str, product: str) -> None:
    """D2: the reversing row must carry the ORIGINAL tender. With a null payment_method the totals
    still net to zero — which is why this hid — but the cash column keeps the sale."""
    today = datetime.now(DUBAI).date().isoformat()
    base = {"shop_id": shop, "product_id": product, "sold_price": "1000", "sold_on": today,
            "recorded_by": 0, "discrepancy": False}

    sale = (await rest(c, "POST", "counter_sales",
                       json={**base, "quantity": 1, "payment_method": "cash",
                             "sold_by": f"{TAG}:sale"}))[0]
    await rest(c, "POST", "counter_sales",
               json={**base, "quantity": -1, "payment_method": sale["payment_method"],
                     "sold_by": f"void:{sale['id']} {TAG}"})

    rows = await rest(c, "GET", f"counter_sales?shop_id=eq.{shop}&select=quantity,sold_price,payment_method")
    split: dict[str, float] = {}
    for r in rows:
        key = r["payment_method"] or "unspecified"
        split[key] = split.get(key, 0) + float(r["sold_price"]) * r["quantity"]

    check("a voided cash sale leaves no cash standing", split.get("cash", 0), 0)
    check("...and nothing lands in 'unspecified'", split.get("unspecified", 0), 0)


async def check_void_restores_stock_and_the_imei(c, shop: str) -> None:
    """T3: products.quantity is the stock source of truth, product_units is the parallel IMEI ledger
    (022). A void has to put BOTH back, or the phone is sold on paper and unsellable in the shop.

    Own product on purpose: the atomicity check above deliberately sells its product down to zero,
    and decrement_stock rightly refuses to go negative — sharing it would test that guard instead."""
    product = (await rest(c, "POST", "products", json={
        "shop_id": shop, "brand": "Postest", "model": f"Imei{uuid4().hex[:6]}", "condition": "New",
        "cost_price": "600", "selling_price": "1000", "quantity": 3, "category": "Mobile",
    }))[0]["id"]

    async def stock() -> int:
        return (await rest(c, "GET", f"products?id=eq.{product}&select=quantity"))[0]["quantity"]

    imei = f"{TAG}-{uuid4().hex[:10]}"
    unit = (await rest(c, "POST", "product_units", json={
        "shop_id": shop, "product_id": product, "imei": imei, "status": "in_stock",
    }))[0]
    opening = await stock()

    # sell it: stock down, unit flips sold and points at the sale
    sale = (await rest(c, "POST", "counter_sales", json={
        "shop_id": shop, "product_id": product, "quantity": 1, "sold_price": "1000",
        "sold_on": datetime.now(DUBAI).date().isoformat(), "recorded_by": 0,
        "payment_method": "cash", "sold_by": f"{TAG}:imei", "discrepancy": False,
    }))[0]
    await rpc(c, "decrement_stock", {"p_id": product, "p_shop": shop, "n": 1})
    await rest(c, "PATCH", f"product_units?id=eq.{unit['id']}",
               json={"status": "sold", "counter_sale_id": sale["id"]})
    check("selling drops stock by one", await stock(), opening - 1)
    sold = (await rest(c, "GET", f"product_units?id=eq.{unit['id']}&select=status"))[0]
    check("...and marks the IMEI sold", sold["status"], "sold")

    # void it: both must come back (mirrors actions/pos.ts voidSale)
    await rest(c, "POST", "counter_sales", json={
        "shop_id": shop, "product_id": product, "quantity": -1, "sold_price": "1000",
        "sold_on": datetime.now(DUBAI).date().isoformat(), "recorded_by": 0,
        "payment_method": sale["payment_method"], "sold_by": f"void:{sale['id']} {TAG}",
        "discrepancy": False,
    })
    await rpc(c, "decrement_stock", {"p_id": product, "p_shop": shop, "n": -1})
    await rest(c, "PATCH", f"product_units?counter_sale_id=eq.{sale['id']}",
               json={"status": "in_stock", "counter_sale_id": None, "sold_at": None})

    check("voiding restores the stock count", await stock(), opening)
    back = (await rest(c, "GET", f"product_units?id=eq.{unit['id']}&select=status,counter_sale_id"))[0]
    check("...and puts the IMEI back in stock", back["status"], "in_stock")
    check("...with no dangling link to the voided sale", back["counter_sale_id"], None)


async def check_credit_note_reverses_the_vat(c, shop: str) -> None:
    """D6 (migration 029): voiding a sale nets its revenue and stock, but the tax invoice it already
    produced stands — FTA reverses a supply with a credit note, never by erasing the document. Every
    report sums invoices.vat_amount, so without the note the void's output VAT stayed collectable."""
    async def vat() -> float:
        rows = await rest(c, "GET", f"invoices?shop_id=eq.{shop}&select=vat_amount")
        return round(sum(float(r["vat_amount"]) for r in rows), 2)

    before = await vat()
    original = (await rest(c, "POST", "invoices", json={
        "shop_id": shop, "source": "counter",
        "items": [{"desc": TAG, "qty": 1, "unit_price": 105.0, "line_total": 105.0}],
        "subtotal": "100.00", "vat_amount": "5.00", "total": "105.00", "created_by": TAG,
    }))[0]
    check("issuing an invoice raises output VAT", await vat(), round(before + 5.0, 2))

    note = (await rest(c, "POST", "invoices", json={
        "shop_id": shop, "source": original["source"],
        "items": [{"desc": TAG, "qty": -1, "unit_price": 105.0, "line_total": -105.0}],
        "subtotal": "-100.00", "vat_amount": "-5.00", "total": "-105.00",
        "credit_of": original["id"], "reason": "Counter sale voided", "created_by": TAG,
    }))[0]
    check("the credit note continues the same per-shop sequence",
          note["invoice_number"], original["invoice_number"] + 1)
    check("the credit note nets the VAT back out", await vat(), before)

    # The two database guards behind it, so the sign can never be faked by a stray row.
    dup = await c.request("POST", f"{_BASE}/rest/v1/invoices", headers=_H, timeout=30, json={
        "shop_id": shop, "invoice_number": 999_999, "source": "counter", "items": [],
        "subtotal": "-1.00", "vat_amount": "-1.00", "total": "-1.00",
        "credit_of": original["id"], "created_by": TAG,
    })
    check("a second credit note for one invoice is refused", dup.status_code >= 400, True,
          f"HTTP {dup.status_code}")
    bad = await c.request("POST", f"{_BASE}/rest/v1/invoices", headers=_H, timeout=30, json={
        "shop_id": shop, "invoice_number": 999_998, "source": "counter", "items": [],
        "subtotal": "-1.00", "vat_amount": "-1.00", "total": "-1.00", "created_by": TAG,
    })
    check("a negative invoice that credits nothing is refused", bad.status_code >= 400, True,
          f"HTTP {bad.status_code}")


async def check_both_channels_reconcile(c, shop: str, product: str) -> None:
    """The headline question: do the Python and the TypeScript agree on one shop's takings once
    both channels are in play? Same rows, two implementations, one number."""
    today = datetime.now(DUBAI).date().isoformat()
    # online: 1500 gross, 50 discount, 20 delivery the shop keeps → 1520 revenue, profit 850
    await rest(c, "POST", "orders", json={
        "shop_id": shop, "product_id": product, "quantity": 1, "selling_price": "1500",
        "discount_amount": "50", "delivery_fee": "20", "status": "delivered",
        "customer_name": f"{TAG} buyer", "phone": "+971500000000", "address": "Marina",
    })
    # counter: 2 units at 300 → 600 revenue, profit 600 − 2×600 … cost is 600/unit so profit −600
    await rest(c, "POST", "counter_sales", json={
        "shop_id": shop, "product_id": product, "quantity": 2, "sold_price": "300",
        "sold_on": today, "recorded_by": 0, "discrepancy": False,
        # 031 requires a terminal reference on every card sale — a card row without one is
        # unreconcilable against the acquirer's statement.
        "payment_method": "card", "payment_ref": f"{TAG}-recon", "sold_by": f"{TAG}:recon",
    })

    start = datetime.combine(datetime.now(DUBAI).date(), datetime.min.time(), tzinfo=DUBAI)
    end = start + timedelta(days=1)

    # --- Python, through the real reporting entry point ---
    from app.orders.service import profit_summary

    py = await profit_summary(UUID(shop), start, end)

    # --- TypeScript, through the dashboard's aggregate() on the same rows ---
    orders = await rest(c, "GET", f"orders?shop_id=eq.{shop}&status=neq.cancelled&status=neq.draft"
                                  "&select=shop_id,created_at,quantity,selling_price,discount_amount,"
                                  "delivery_fee,products(cost_price,brand,model,tags)")
    counter = await rest(c, "GET", f"counter_sales?shop_id=eq.{shop}"
                                   "&select=shop_id,sold_on,quantity,sold_price,discount_amount,"
                                   "discrepancy,payment_method,products(cost_price,brand,model)")
    ts = run_ts_aggregate({
        "orders": orders, "counter": counter, "catalogue": [], "vat": [], "cancelled": [],
        "shops": [{"id": shop, "rider_keeps_delivery": False}],
    })

    # Compare to the fils — Decimal on one side, JS number on the other, so quantise both.
    def fils(x) -> Decimal:
        return Decimal(str(x)).quantize(Decimal("0.01"))

    check("Python and TypeScript agree on revenue",
          fils(py.revenue), fils(ts["revenue"]), "the whole point of the exercise")
    check("Python and TypeScript agree on profit", fils(py.profit), fils(ts["profit"]))
    check("online + counter = headline (TypeScript)",
          round(ts["onlineRevenue"] + ts["counterRevenue"], 2), round(ts["revenue"], 2))
    check("counter revenue is per-unit × qty", ts["counterRevenue"], 600)
    check("the delivery the shop keeps is in revenue", ts["onlineRevenue"], 1520)


async def check_cod_equals_the_invoice_total(c, shop: str, product: str) -> None:
    """The invariant that ties the two languages together on the online channel.

    Python grosses the COD up (orders/service.py::assign_delivery) and TypeScript grosses the
    invoice up (actions/invoices.ts) — independently, from the same ex-VAT columns. If the two
    rounding rules ever drift, the rider hands over a different number than the document says and
    the shop is short on every delivery. Checked at prices chosen to land on a half-fil.
    """
    from app.utils.vat import with_vat

    for sell, disc, fee in [("1350", "0", "0"), ("1500", "50", "20"), ("3399", "199", "15"),
                            ("0.01", "0", "0"), ("9999.99", "0.99", "9.99")]:
        net = Decimal(sell) - Decimal(disc) + Decimal(fee)
        py_cod = with_vat(net)
        ts = run_ts(
            'import { vatOnNet } from "./money.ts";\n'
            "const i = JSON.parse(process.argv[2]);\n"
            "const sub = Math.round((i.sell - i.disc + i.fee) * 100) / 100;\n"
            "const vat = vatOnNet(sub);\n"
            "process.stdout.write(JSON.stringify({ total: Math.round((sub + vat) * 100) / 100 }));\n",
            {"sell": float(sell), "disc": float(disc), "fee": float(fee)},
        )
        check(f"COD == invoice total at {sell}/{disc}/{fee}",
              py_cod, Decimal(str(ts["total"])).quantize(Decimal("0.01")))


async def check_vat_on_top_and_the_discount_lands(c, shop: str, product: str) -> None:
    """030: products.selling_price is stored BEFORE VAT, so the till adds 5% on top and records the
    giveaway beside the price. Two things have to hold at once — the customer's total is
    (gross − discount) × 1.05 to the fil, and revenue in every report stays GROSS with the discount
    reported separately, exactly as an online order behaves."""
    today = datetime.now(DUBAI).date().isoformat()
    from app.orders.service import profit_summary

    start = datetime.combine(datetime.now(DUBAI).date(), datetime.min.time(), tzinfo=DUBAI)
    end = start + timedelta(days=1)
    before = await profit_summary(UUID(shop), start, end)

    # The arithmetic comes from the shipping helpers, not a copy of them living in this script.
    lines = [{"unit": 1000, "qty": 1}, {"unit": 500, "qty": 2}]  # gross 2000
    money = run_ts(
        'import { allocateDiscount, vatOnNet } from "./money.ts";\n'
        "const i = JSON.parse(process.argv[2]);\n"
        "const totals = i.lines.map((l) => Math.round(l.unit * l.qty * 100) / 100);\n"
        "const gross = totals.reduce((s, v) => s + v, 0);\n"
        "const subtotal = Math.round((gross - i.discount) * 100) / 100;\n"
        "const vat = vatOnNet(subtotal);\n"
        "process.stdout.write(JSON.stringify({ totals, gross, subtotal, vat,\n"
        "  total: Math.round((subtotal + vat) * 100) / 100,\n"
        "  shares: allocateDiscount(i.discount, totals) }));\n",
        {"lines": lines, "discount": 150},
    )

    check("VAT is added on top of the stored price", money["vat"], 92.5, "5% of 1850, not 5/105")
    check("the customer pays net + VAT", money["total"], 1942.5)
    check("the split discount adds back to what was given",
          round(sum(money["shares"]), 2), 150.0)

    for line, share in zip(lines, money["shares"]):
        await rest(c, "POST", "counter_sales", json={
            "shop_id": shop, "product_id": product, "quantity": line["qty"],
            "sold_price": f"{line['unit']:.2f}", "discount_amount": f"{share:.2f}",
            "sold_on": today, "recorded_by": 0, "discrepancy": False,
            "payment_method": "cash", "sold_by": f"{TAG}:vat",
        })
    inv = (await rest(c, "POST", "invoices", json={
        "shop_id": shop, "source": "counter",
        "items": [{"desc": TAG, "qty": l["qty"], "unit_price": l["unit"], "line_total": t}
                  for l, t in zip(lines, money["totals"])],
        "subtotal": f"{money['subtotal']:.2f}", "discount": "150.00",
        "vat_amount": f"{money['vat']:.2f}", "total": f"{money['total']:.2f}",
        "created_by": TAG,
    }))[0]
    check("the invoice's parts add up to its total",
          round(float(inv["subtotal"]) + float(inv["vat_amount"]), 2), float(inv["total"]))
    check("the document carries the discount it gave",
          round(float(inv["subtotal"]) + float(inv["discount"]), 2), money["gross"])

    after = await profit_summary(UUID(shop), start, end)
    check("revenue stays GROSS in /profit", after.revenue - before.revenue, Decimal("2000.00"))
    check("...and the discount is reported beside it",
          after.discounts - before.discounts, Decimal("150.00"))


def run_ts_aggregate(payload: dict) -> dict:
    return run_ts(
        'import { aggregate } from "./profit-math.ts";\n'
        "const input = JSON.parse(process.argv[2]);\n"
        "process.stdout.write(JSON.stringify(aggregate(input)));\n",
        payload,
    )


def run_ts(src: str, payload: dict) -> dict:
    """Drive dashboard TypeScript in-place, so this compares against the code that ships."""
    driver = DASHBOARD / "lib" / "__probe.mts"
    driver.write_text(src, encoding="utf-8")
    try:
        out = subprocess.run(
            ["node", str(driver), json.dumps(payload)],
            capture_output=True, text=True, cwd=DASHBOARD, timeout=120,
        )
        if out.returncode != 0:
            raise RuntimeError(f"node failed: {out.stderr[-800:]}")
        return json.loads(out.stdout)
    finally:
        driver.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# fixture lifecycle
# ---------------------------------------------------------------------------
async def seed(c, made: dict) -> str:
    """Records each id in `made` as it goes, so a half-finished seed still tears itself down."""
    client = (await rest(c, "POST", "clients", json={
        "name": f"{TAG} client {uuid4().hex[:6]}", "status": "active",
    }))[0]
    made["client"] = client["id"]

    shop = (await rest(c, "POST", "shops", json={
        "client_id": client["id"], "name": f"{TAG} shop {uuid4().hex[:6]}",
        "whatsapp_number": f"+9999{uuid4().int % 10**7:07d}", "status": "active",
        "rider_keeps_delivery": False,
    }))[0]
    made["shop"] = shop["id"]

    product = (await rest(c, "POST", "products", json={
        "shop_id": shop["id"], "brand": "Postest", "model": "Rig", "condition": "New",
        "cost_price": "600", "selling_price": "1000", "quantity": 5, "category": "Mobile",
    }))[0]
    made["product"] = product["id"]
    return product["id"]


async def teardown(c, made: dict) -> None:
    """Children first — orders and counter_sales reference products with ON DELETE RESTRICT."""
    shop, client = made.get("shop"), made.get("client")
    paths = []
    if shop:
        paths += [f"{t}?shop_id=eq.{shop}" for t in (
            "invoices", "product_units", "counter_sales", "orders", "daily_counters",
            "invoice_counters", "cod_ledger", "products",
        )]
        paths.append(f"shops?id=eq.{shop}")
    if client:
        paths.append(f"clients?id=eq.{client}")

    for path in paths:
        try:
            await rest(c, "DELETE", path)
        except httpx.HTTPStatusError as e:
            print(f"  cleanup warning on {path.split('?')[0]}: "
                  f"{e.response.status_code} {e.response.text[:140]}")


async def main() -> int:
    async with httpx.AsyncClient() as c:
        before = await counts(c)
        made: dict[str, str] = {}
        try:
            product = await seed(c, made)
            shop = made["shop"]
            print(f"\ndisposable shop {shop}\n")

            print("stock integrity")
            await check_stock_is_atomic(c, shop, product)
            print("invoice numbering")
            await check_invoice_numbers_are_gap_free(c, shop)
            await check_day_seq_per_kind(c, shop)
            print("counter sale voids")
            await check_void_reverses_in_its_own_bucket(c, shop, product)
            await check_void_restores_stock_and_the_imei(c, shop)
            print("tax credit notes")
            await check_credit_note_reverses_the_vat(c, shop)
            print("cross-channel reconciliation")
            await check_both_channels_reconcile(c, shop, product)
            # Last: it adds counter rows, and the reconciliation above asserts exact totals.
            print("VAT on top + POS discounts")
            await check_vat_on_top_and_the_discount_lands(c, shop, product)
            await check_cod_equals_the_invoice_total(c, shop, product)
        finally:
            await teardown(c, made)
            after = await counts(c)
            check("every table returns to its pre-run row count", after, before)

    print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
    return 1 if failures else 0


async def counts(c) -> dict[str, int]:
    out = {}
    for t in ("clients", "shops", "products", "orders", "counter_sales", "invoices", "product_units"):
        r = await c.get(f"{_BASE}/rest/v1/{t}", headers={**_H, "Prefer": "count=exact",
                                                         "Range": "0-0"},
                        params={"select": "id"}, timeout=30)
        out[t] = int(r.headers["content-range"].split("/")[-1])
    return out


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
