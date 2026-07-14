"""Report formatting (SPEC §6, §12). Pure: period arg → date range, summary → text.

Kept out of `orders/` because it's presentation, not money logic. The AED/monospace layout is
the only thing here; the numbers come from `orders.profit_summary`.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from app.orders.models import ProfitSummary

# The business runs in the UAE only, so days are Asia/Dubai days (+4, no DST). A report for
# "today" must end at Dubai midnight, not UTC midnight — otherwise the last 4h of local sales
# land in the wrong day. created_at is timestamptz (UTC); comparing against +04:00 boundaries
# lets Postgres map the local-day window to the right UTC instants.
DUBAI = timezone(timedelta(hours=4))


def parse_period(arg: str, today: date | None = None) -> tuple[datetime, datetime, str]:
    """Map a /profit argument to an Asia/Dubai [start, end) window + a human label.

    today | yesterday | weekly (last 7d) | monthly (this month) | YYYY-MM-DD. Default: today.
    """
    today = today or datetime.now(DUBAI).date()
    arg = (arg or "today").strip().lower()

    if arg in ("", "today"):
        return _day(today), _day(today + timedelta(days=1)), f"Today ({today:%b %d, %Y})"
    if arg == "yesterday":
        y = today - timedelta(days=1)
        return _day(y), _day(today), f"Yesterday ({y:%b %d, %Y})"
    if arg == "weekly":
        start = today - timedelta(days=6)
        return _day(start), _day(today + timedelta(days=1)), f"Last 7 days ({start:%b %d} – {today:%b %d})"
    if arg == "monthly":
        first = today.replace(day=1)
        return _day(first), _day(today + timedelta(days=1)), f"This month ({today:%B %Y})"
    try:
        d = date.fromisoformat(arg)
    except ValueError:
        raise ValueError(f"Unknown period '{arg}'. Use today, yesterday, weekly, monthly, or YYYY-MM-DD.")
    return _day(d), _day(d + timedelta(days=1)), f"{d:%b %d, %Y}"


def _day(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=DUBAI)


def _aed(x) -> str:
    return f"{x:,.0f} AED"


def format_profit(s: ProfitSummary, label: str) -> str:
    """SPEC §6 profit report — monospace, emojis, AED."""
    lines = [
        f"📊 Profit Report — {label}",
        "",
        f"🧾 Orders:       {s.orders}",
        f"💵 Revenue:      {_aed(s.revenue)}",
        f"🏷 Discounts:    {_aed(s.discounts)}",
        f"📦 Cost:         {_aed(s.cost)}",
        f"✅ Gross Profit: {_aed(s.profit)}",
        f"📈 Margin:       {s.margin:.1f}%",
    ]
    if s.top:
        lines += ["", "Top products:"]
        lines += [f"  {i}. {l.label} — {l.qty} sold, +{_aed(l.profit)}" for i, l in enumerate(s.top, 1)]
    if s.clearance_profit:
        lines += ["", f"🧹 Clearance profit: +{_aed(s.clearance_profit)}"]
    return "\n".join(lines)


def format_owner_profit(items: list[tuple[str, ProfitSummary]], label: str) -> str:
    """Owner view: one line per shop + a combined total (SPEC §6 /owner profit all|compare)."""
    lines = [f"🏢 Owner Profit — {label}", ""]
    total = ProfitSummary()
    for name, s in items:
        lines.append(f"  {name}: {s.orders} orders · +{_aed(s.profit)} ({s.margin:.1f}%)")
        total = ProfitSummary(
            total.orders + s.orders,
            total.revenue + s.revenue,
            total.discounts + s.discounts,
            total.cost + s.cost,
            total.profit + s.profit,
        )
    lines += ["", f"Σ All shops: {total.orders} orders · +{_aed(total.profit)} ({total.margin:.1f}%)"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shop-owner bot views (remote oversight — the owner audits without visiting).
# Pure text over rows the services already return; no queries here.
# ---------------------------------------------------------------------------


def format_top_products(items: list[tuple[str, ProfitSummary]], label: str, n: int = 10) -> str:
    """Merge every shop's top-5 profit lines into one owner-wide top-N by profit."""
    merged: dict[str, list] = {}  # label -> [qty, profit]
    for _shop, s in items:
        for line in s.top:
            entry = merged.setdefault(line.label, [0, 0])
            entry[0] += line.qty
            entry[1] += line.profit
    ranked = sorted(merged.items(), key=lambda kv: kv[1][1], reverse=True)[:n]
    lines = [f"🏆 Top products — {label}", ""]
    if not ranked:
        return lines[0] + "\n\nNo sales in this period."
    lines += [
        f"  {i}. {name} — {qty} sold, +{_aed(profit)}"
        for i, (name, (qty, profit)) in enumerate(ranked, 1)
    ]
    return "\n".join(lines)


def _cancel_remark(row: dict) -> str:
    """Remarks live in two places: orders.cancel_remarks (rider cancels) or the 'cancelled'
    history row's changed_by (shop rejections). Whichever exists wins."""
    if row.get("cancel_remarks"):
        return str(row["cancel_remarks"])
    for h in row.get("order_status_history") or []:
        if h.get("status") == "cancelled" and h.get("changed_by"):
            return str(h["changed_by"])
    return "no remarks"


def _item_of(row: dict) -> str:
    p = row.get("products") or {}
    return f"{p.get('brand', '?')} {p.get('model', '')}".strip()


def format_audit_report(per_shop: list[tuple[str, list[dict], list[dict]]], label: str) -> str:
    """THE anti-corruption view: every cancellation (with remarks) + every discount, per shop."""
    lines = [f"🕵️ Cancellations & discounts — {label}"]
    n_cancel, total_discount = 0, 0.0
    for shop_name, cancelled, discounted in per_shop:
        lines += ["", f"🏪 {shop_name}"]
        if not cancelled and not discounted:
            lines.append("  ✅ no cancellations, no discounts")
            continue
        for row in cancelled:
            n_cancel += 1
            lines.append(
                f"  ❌ #{row.get('order_number', '?')} — {_item_of(row)} ×{row.get('quantity', 1)}"
                f" — remarks: {_cancel_remark(row)}"
            )
        for row in discounted:
            disc = float(row.get("discount_amount") or 0)
            total_discount += disc
            lines.append(
                f"  🏷 #{row.get('order_number', '?')} — {_item_of(row)}"
                f" — {_aed(disc)} off {_aed(float(row.get('selling_price') or 0))}"
                f" ({row.get('status', '?')})"
            )
    lines += ["", f"Σ {n_cancel} cancellation(s) · {_aed(total_discount)} discounts given"]
    return "\n".join(lines)


def format_inventory(shop_name: str, rows: list[dict]) -> str:
    """Stock list, low stock first (that's the query order). ⚠️ at qty ≤ 2."""
    if not rows:
        return f"🗃 {shop_name}: no products."
    lines = [f"🗃 Inventory — {shop_name}", ""]
    units, value = 0, 0.0
    for row in rows:
        qty = int(row.get("quantity") or 0)
        units += qty
        value += qty * float(row.get("cost_price") or 0)
        color = f" ({row['color']})" if row.get("color") else ""
        warn = "  ⚠️ LOW" if qty <= 2 else ""
        lines.append(
            f"  {row.get('brand', '?')} {row.get('model', '')}{color}"
            f" — {qty} × {_aed(float(row.get('selling_price') or 0))}{warn}"
        )
    lines += ["", f"Σ {units} unit(s) · stock value {_aed(value)} (at cost)"]
    return "\n".join(lines)


def format_cod_outstanding(per_shop: list[tuple[str, list[tuple[str, object]]]]) -> str:
    """Cash riders are still holding, per rider per shop + grand total. 0-balance riders shown
    too — the owner should see the full roster, not only debtors."""
    lines = ["💵 COD outstanding (cash with riders)"]
    grand = 0.0
    for shop_name, riders in per_shop:
        lines += ["", f"🏪 {shop_name}"]
        if not riders:
            lines.append("  no riders")
            continue
        for rider_name, balance in riders:
            bal = float(balance)
            grand += bal
            flag = " ⚠️" if bal > 0 else ""
            lines.append(f"  🛵 {rider_name}: {_aed(bal)}{flag}")
    lines += ["", f"Σ All shops: {_aed(grand)} outstanding"]
    return "\n".join(lines)


if __name__ == "__main__":  # self-check: python -m app.reports.service
    from decimal import Decimal

    from app.orders.models import ProfitLine

    s1 = ProfitSummary(top=[ProfitLine("iPhone 15", 2, Decimal("500"))])
    s2 = ProfitSummary(top=[ProfitLine("iPhone 15", 1, Decimal("250")), ProfitLine("S24", 1, Decimal("300"))])
    t = format_top_products([("A", s1), ("B", s2)], "Today")
    assert "1. iPhone 15 — 3 sold, +750 AED" in t  # merged across shops, ranked by profit
    assert "2. S24" in t

    cancelled = [{"order_number": 7, "quantity": 1, "products": {"brand": "Apple", "model": "iPhone"},
                  "cancel_remarks": None,
                  "order_status_history": [{"status": "cancelled", "changed_by": "customer refused"}]}]
    discounted = [{"order_number": 8, "selling_price": 1000, "discount_amount": 150,
                   "status": "delivered", "products": {"brand": "Apple", "model": "iPhone"}}]
    a = format_audit_report([("Shop 01", cancelled, discounted), ("Shop 02", [], [])], "Today")
    assert "remarks: customer refused" in a  # remark recovered from history when cancel_remarks empty
    assert "150 AED off 1,000 AED" in a and "✅ no cancellations" in a
    assert "Σ 1 cancellation(s) · 150 AED discounts given" in a

    inv = format_inventory("Shop 01", [{"brand": "Apple", "model": "iPhone", "color": "black",
                                        "quantity": 1, "selling_price": 1000, "cost_price": 800}])
    assert "⚠️ LOW" in inv and "stock value 800 AED" in inv

    c = format_cod_outstanding([("Shop 01", [("Ali", Decimal("120")), ("Omar", Decimal("0"))])])
    assert "Ali: 120 AED ⚠️" in c and "Omar: 0 AED\n" in c + "\n"
    assert "Σ All shops: 120 AED outstanding" in c
    print("report formatters ok")
