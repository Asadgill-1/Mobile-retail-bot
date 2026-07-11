"""Report formatting (SPEC §6, §12). Pure: period arg → date range, summary → text.

Kept out of `orders/` because it's presentation, not money logic. The AED/monospace layout is
the only thing here; the numbers come from `orders.profit_summary`.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from app.orders.models import ProfitSummary


def parse_period(arg: str, today: date | None = None) -> tuple[datetime, datetime, str]:
    """Map a /profit argument to a UTC [start, end) window + a human label.

    today | yesterday | weekly (last 7d) | monthly (this month) | YYYY-MM-DD. Default: today.
    """
    today = today or datetime.now(timezone.utc).date()
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
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


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
