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


# --- product stats (Q-014) ---
from app.reports.service import format_product_stats  # noqa: E402


def _stat(code, label, sold, revenue, profit, stock):
    from decimal import Decimal

    return {"code": code, "label": label, "sold_qty": sold,
            "revenue": Decimal(str(revenue)), "profit": Decimal(str(profit)), "stock": stock}


def test_format_product_stats_sold_then_dead_stock():
    rows = [_stat("PR0001", "Samsung S23", 2, 4000, 900, 4),
            _stat("PR0002", "Nokia 3310", 0, 0, 0, 7)]
    out = format_product_stats("Shop 01", rows, "This week")

    assert "PR0001 · Samsung S23 — 2 sold" in out
    assert "profit" in out and "stock 4" in out
    assert "💤 No sales (1)" in out and "PR0002 · Nokia 3310 — stock 7" in out


def test_format_product_stats_nothing_sold_says_so():
    out = format_product_stats("Shop 01", [_stat("PR0001", "Nokia 3310", 0, 0, 0, 7)], "Today")
    assert "nothing sold in this period" in out
    assert "💤 No sales (1)" in out


def test_format_product_stats_empty_catalogue():
    assert "no products" in format_product_stats("Shop 01", [], "Today")


# --- activity logs (📋): the shop owner must be able to read what staff did ---
from app.reports.service import format_activity  # noqa: E402


def _log(action, actor="555", detail=None, at="2026-07-12T10:22:00+00:00"):
    return {"action": action, "actor": actor, "detail": detail or {}, "created_at": at}


def test_format_activity_humanizes_actions_and_names_actors():
    rows = [
        _log("kconf", detail={"args": ["7"]}),
        _log("kneg", detail={"args": ["off"]}),
        _log("racc", actor="999", detail={"args": ["7"]}),
    ]
    out = format_activity("Shop 01", rows, {"555": "Ali", "999": "Sami"})

    assert "Ali — confirmed order #7" in out
    assert "Ali — turned negotiation off" in out
    assert "Sami — confirmed pickup of order #7" in out
    assert "14:22" in out  # Dubai time (UTC+4), not raw UTC


def test_format_activity_humanizes_dashboard_pos_actions():
    rows = [
        _log("dcsale", actor="dashboard:k@shop.local", detail={"args": ["3"]}),
        _log("dvoid", actor="dashboard:k@shop.local", detail={"args": ["ab12"]}),
        _log("dinv", actor="dashboard:k@shop.local", detail={"args": ["000042"]}),
    ]
    out = format_activity("Shop 01", rows, {})
    assert "recorded a counter sale of 3 item(s)" in out
    assert "voided counter sale ab12" in out
    assert "issued invoice INV-000042" in out


def test_format_activity_slash_and_button_read_the_same():
    button = format_activity("S", [_log("kconf", detail={"args": ["7"]})], {})
    command = format_activity("S", [_log("confirmorder_cmd", detail={"args": ["7"]})], {})
    assert "confirmed order #7" in button and "confirmed order #7" in command


def test_format_activity_unknown_action_still_shows():
    # An unmapped action must never vanish from the log — that's the one worth seeing.
    out = format_activity("S", [_log("mystery_cmd", detail={"text": "/mystery 1"})], {})
    assert "mystery_cmd" in out and "/mystery 1" in out


def test_format_activity_mapped_action_missing_args_does_not_crash():
    out = format_activity("S", [_log("kconf", detail={})], {})
    assert "confirmed order" in out


def test_format_activity_unknown_actor_shows_raw_id():
    out = format_activity("S", [_log("kconf", actor="777", detail={"args": ["1"]})], {})
    assert "777 — confirmed order #1" in out


def test_format_activity_bad_timestamp_and_empty():
    assert "no activity" in format_activity("S", [], {})
    out = format_activity("S", [_log("kconf", detail={"args": ["1"]}, at="junk")], {})
    assert "confirmed order #1" in out  # no crash


# --- counter sales report (🧾) ---
from app.reports.service import format_counter_sales  # noqa: E402


def _csale(qty, unit, sold_on="2026-07-12", brand="Samsung", model="S23", flagged=False):
    return {"quantity": qty, "sold_price": str(unit), "sold_on": sold_on,
            "discrepancy": flagged, "products": {"brand": brand, "model": model}}


def test_format_counter_sales_groups_by_day_and_totals():
    per_shop = [("Shop 01", [_csale(2, 3400), _csale(1, 5000, sold_on="2026-07-13")])]
    out = format_counter_sales(per_shop, "This week")
    assert "🗓 2026-07-12" in out and "🗓 2026-07-13" in out
    assert "Samsung S23 ×2" in out
    assert "11,800 AED counter revenue" in out  # 6800 + 5000


def test_format_counter_sales_flags_discrepancies_and_excludes_them_from_the_total():
    per_shop = [("Shop 01", [_csale(1, 100), _csale(9, 100, flagged=True)])]
    out = format_counter_sales(per_shop, "Today")
    assert "stock didn't cover this — not counted" in out
    assert "100 AED counter revenue" in out       # the flagged 900 is NOT in the money
    assert "1 row(s) flagged" in out


def test_format_counter_sales_empty_shop():
    out = format_counter_sales([("Shop 01", [])], "Today")
    assert "no counter sales" in out and "0 AED counter revenue" in out


def test_format_profit_shows_counter_line_only_when_there_are_counter_sales():
    from decimal import Decimal

    from app.orders.models import ProfitSummary
    from app.reports.service import format_profit

    online_only = ProfitSummary(orders=1, revenue=Decimal("100"), cost=Decimal("60"),
                                profit=Decimal("40"))
    assert "Counter sales" not in format_profit(online_only, "Today")

    both = ProfitSummary(orders=2, revenue=Decimal("200"), cost=Decimal("120"),
                         profit=Decimal("80"), counter_revenue=Decimal("100"),
                         counter_profit=Decimal("40"))
    out = format_profit(both, "Today")
    assert "Counter sales" in out and "included above" in out
