"""Profit math (SPEC §6) — pure, no IO. The money path: tested, never simplified away.

SPEC formula: profit per order = selling_price - discount_amount - product.cost_price.
`selling_price` is the total charged for the line; `cost_price` is per-unit, so real cost is
`cost_price * quantity`. Using per-unit cost without the quantity would under-count cost on any
multi-unit order — a silent money bug. We multiply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class ProfitLine:
    label: str  # "Samsung Galaxy S23"
    qty: int
    profit: Decimal


@dataclass(frozen=True)
class ProfitSummary:
    orders: int = 0
    revenue: Decimal = Decimal("0")
    discounts: Decimal = Decimal("0")
    cost: Decimal = Decimal("0")
    profit: Decimal = Decimal("0")
    clearance_profit: Decimal = Decimal("0")
    top: list[ProfitLine] = field(default_factory=list)

    @property
    def margin(self) -> float:
        """Gross margin % over cost. 0 when there is no cost (no orders)."""
        return float(self.profit / self.cost * 100) if self.cost else 0.0


def line_profit(selling_price: Decimal, discount: Decimal, cost_price: Decimal, qty: int) -> Decimal:
    """Profit for one order line (SPEC §6). cost is per-unit → × quantity."""
    return (selling_price - discount) - cost_price * qty


if __name__ == "__main__":  # ponytail: one runnable check on the money path
    d = Decimal
    assert line_profit(d("2499"), d("0"), d("2000"), 1) == d("499")
    assert line_profit(d("5000"), d("200"), d("2000"), 2) == d("800")  # qty=2: cost 4000
    s = ProfitSummary(orders=1, revenue=d("2499"), cost=d("2000"), profit=d("499"))
    assert round(s.margin, 1) == 24.9
    assert ProfitSummary().margin == 0.0  # no divide-by-zero on empty
    print("profit math ok")
