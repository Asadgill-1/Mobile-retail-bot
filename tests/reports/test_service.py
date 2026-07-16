"""Report formatting (SPEC §6): period parsing + text layout. Pure."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.orders.models import ProfitLine, ProfitSummary
from app.reports.service import format_owner_profit, format_profit, parse_period

TODAY = date(2026, 7, 9)


def test_parse_period_windows():
    s, e, label = parse_period("today", TODAY)
    assert (s.date(), e.date()) == (date(2026, 7, 9), date(2026, 7, 10)) and "Today" in label
    # UAE-only: day boundaries are Asia/Dubai (+4), not UTC — so 20:00–24:00 local sales fall in
    # the right day. Guards against a silent revert to UTC midnight.
    assert s.utcoffset() == timedelta(hours=4)

    s, e, _ = parse_period("yesterday", TODAY)
    assert (s.date(), e.date()) == (date(2026, 7, 8), date(2026, 7, 9))

    s, e, _ = parse_period("weekly", TODAY)
    assert (s.date(), e.date()) == (date(2026, 7, 3), date(2026, 7, 10))  # last 7 days incl today

    s, e, _ = parse_period("monthly", TODAY)
    assert s.date() == date(2026, 7, 1)

    s, e, label = parse_period("2026-06-15", TODAY)
    assert (s.date(), e.date()) == (date(2026, 6, 15), date(2026, 6, 16))


def test_parse_period_defaults_to_today_and_rejects_junk():
    assert "Today" in parse_period("", TODAY)[2]
    with pytest.raises(ValueError):
        parse_period("last-tuesday", TODAY)


def test_format_profit_shows_money_margin_and_top():
    s = ProfitSummary(
        orders=2, revenue=Decimal("7499"), discounts=Decimal("200"),
        cost=Decimal("6000"), profit=Decimal("1299"), clearance_profit=Decimal("300"),
        top=[ProfitLine("Samsung S23", 2, Decimal("999"))],
    )
    out = format_profit(s, "Today")
    assert "7,499 AED" in out and "1,299 AED" in out
    assert "21.6%" in out  # 1299/6000*100 = 21.65 → 21.6 at 1dp
    assert "Samsung S23 — 2 sold, +999 AED" in out
    assert "Clearance profit: +300 AED" in out


def test_format_owner_profit_totals_across_shops():
    a = ProfitSummary(orders=1, cost=Decimal("2000"), profit=Decimal("500"))
    b = ProfitSummary(orders=2, cost=Decimal("3000"), profit=Decimal("700"))
    out = format_owner_profit([("Shop A", a), ("Shop B", b)], "Today")
    assert "Shop A: 1 orders" in out and "Shop B: 2 orders" in out
    assert "All shops: 3 orders · +1,200 AED" in out


# --- 🆔 ID list (migration 010): the codes a human types back ---
from app.reports.service import format_id_list_products, format_id_list_riders  # noqa: E402


def test_format_id_list_products_shows_codes_not_uuids():
    rows = [
        {"product_number": 1, "brand": "Samsung", "model": "S23", "color": "black", "quantity": 3},
        {"product_number": 12, "brand": "Apple", "model": "iPhone 15", "quantity": 0},
    ]
    out = format_id_list_products("Shop 01", rows)
    assert "PR0001 · Samsung S23 (black) — qty 3" in out
    assert "PR0012 · Apple iPhone 15 — qty 0" in out
    assert "/boost" in out  # tells the keeper what to do with the code


def test_format_id_list_products_unnumbered_row_shows_dash():
    # Before migration 010 backfills, product_number is null — must not crash or print "None".
    out = format_id_list_products("Shop 01", [{"brand": "Nokia", "model": "3310", "quantity": 5}])
    assert "— · Nokia 3310" in out or "—" in out
    assert "None" not in out


def test_format_id_list_riders_shows_codes_and_link_state():
    rows = [
        {"rider_number": 1, "name": "Sami", "phone": "0501234567", "telegram_id": 999},
        {"rider_number": 2, "name": "Ali", "phone": "0507654321", "telegram_id": None},
    ]
    out = format_id_list_riders("Shop 01", rows)
    assert "rider001 · Sami — 0501234567 🟢" in out
    assert "rider002 · Ali — 0507654321 ⚪" in out


def test_format_id_lists_empty_states():
    assert "no products" in format_id_list_products("Shop 01", [])
    assert "no riders" in format_id_list_riders("Shop 01", [])
