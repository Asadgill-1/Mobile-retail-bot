"""UAE VAT — 5%, charged ON TOP of every stored price.

`products.selling_price`, `orders.selling_price`, `orders.discount_amount` and
`counter_sales.sold_price` all hold money BEFORE VAT. That is what a shop types into inventory,
what a shopkeeper's own notices and reports quote, and what every revenue figure means — VAT is
collected for the FTA and was never the shop's income, so folding it into revenue would overstate
every margin by 5%.

The 5% is added at exactly two kinds of boundary:

  * anything a CUSTOMER is quoted, offered or charged — so a price they hear is the price they pay;
  * the cash a RIDER collects (`orders.cod_amount`), which is real money changing hands.

and taken back off at one: a price the customer NAMES while haggling arrives VAT-inclusive and has
to become net before it can be compared to a floor, a `min_price` or a list price.

The dashboard's TypeScript half of this lives in `lib/money.ts` (`vatOnNet` / `vatFromInclusive`);
the two must agree to the fil or an invoice will not match the order it came from.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

VAT_RATE = Decimal("5")
_MULT = Decimal("1") + VAT_RATE / Decimal("100")
_FILS = Decimal("0.01")


def with_vat(net: Decimal | float | str | None) -> Decimal:
    """Net (ex-VAT) → what the customer actually pays."""
    return (Decimal(str(net or 0)) * _MULT).quantize(_FILS, rounding=ROUND_HALF_UP)


def without_vat(gross: Decimal | float | str | None) -> Decimal:
    """A VAT-inclusive figure the customer named → the net the shop's own numbers are in."""
    return (Decimal(str(gross or 0)) / _MULT).quantize(_FILS, rounding=ROUND_HALF_UP)


def money(v: Decimal) -> str:
    """Money for a message: whole dirhams stay whole, fils show when the VAT creates them."""
    q = Decimal(str(v)).quantize(_FILS, rounding=ROUND_HALF_UP)
    return str(q.to_integral_value()) if q == q.to_integral_value() else str(q)


if __name__ == "__main__":  # ponytail: one runnable check on the money path
    assert with_vat(1350) == Decimal("1417.50"), with_vat(1350)
    assert with_vat("1000") == Decimal("1050.00")
    assert without_vat("1417.50") == Decimal("1350.00")
    assert without_vat(with_vat("2499")) == Decimal("2499.00")
    # A customer's round budget maps to a net the catalogue can actually be filtered by.
    assert without_vat(2000) == Decimal("1904.76")
    assert money(with_vat(1000)) == "1050"
    assert money(with_vat(1350)) == "1417.50"
    print("vat ok")
