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
