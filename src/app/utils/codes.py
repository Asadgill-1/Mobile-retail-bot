"""Human-readable reference codes for products and riders.

The DB carries a global `bigint` identity per row (products.product_number,
delivery_persons.rider_number — migration 010). These pure functions render those
integers as the short codes a shopkeeper actually types ("PR0001", "rider001") and
parse whatever the shopkeeper types back to the integer.

Parsers are forgiving: "PR0001", "pr1", "  RIDER007 ", or a plain "1" all resolve.
Anything that isn't a positive integer under the (optional) prefix → None (the caller
turns None into the same "not found" it uses for a wrong UUID — never a crash).
"""

from __future__ import annotations

import re

_PRODUCT_PREFIX = "PR"
_RIDER_PREFIX = "rider"


def product_code(n: int) -> str:
    """1 → 'PR0001'."""
    return f"{_PRODUCT_PREFIX}{n:04d}"


def rider_code(n: int) -> str:
    """1 → 'rider001'."""
    return f"{_RIDER_PREFIX}{n:03d}"


def _parse(raw: str, prefix: str) -> int | None:
    """Strip an optional case-insensitive prefix, read the rest as a positive int."""
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    s = re.sub(f"^{prefix}", "", s, count=1, flags=re.IGNORECASE).strip()
    if not s.isdigit():
        return None
    n = int(s)
    return n if n > 0 else None


def parse_product_code(raw: str) -> int | None:
    """'PR0001' | 'pr1' | '1' → 1; junk → None."""
    return _parse(raw, _PRODUCT_PREFIX)


def parse_rider_code(raw: str) -> int | None:
    """'rider001' | 'RIDER7' | '7' → 7; junk → None."""
    return _parse(raw, _RIDER_PREFIX)


if __name__ == "__main__":
    assert product_code(1) == "PR0001"
    assert product_code(1234) == "PR1234"
    assert rider_code(1) == "rider001"
    assert rider_code(42) == "rider042"

    assert parse_product_code("PR0001") == 1
    assert parse_product_code("pr1") == 1
    assert parse_product_code("  PR0042 ") == 42
    assert parse_product_code("7") == 7
    assert parse_product_code("rider1") is None  # wrong prefix stays in the digits → not a number
    assert parse_product_code("") is None
    assert parse_product_code("PR") is None
    assert parse_product_code("PR0") is None  # 0 is not a valid row number
    assert parse_product_code("abc") is None

    assert parse_rider_code("rider001") == 1
    assert parse_rider_code("RIDER7") == 7
    assert parse_rider_code("7") == 7
    assert parse_rider_code("PR1") is None
    assert parse_rider_code("rider0") is None
    print("codes self-check OK")
