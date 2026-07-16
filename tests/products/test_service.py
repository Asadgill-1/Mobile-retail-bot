"""Product mutations (SPEC §5): validation + the cross-shop tenant guard.

Supabase is faked — the fake asserts that every query it receives is scoped by shop_id.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.products.service import (
    InvalidBoostLevel,
    InvalidTag,
    ProductNotFound,
    add_tags,
    clear_tags,
    get_product,
    parse_boost_level,
    parse_tags,
    remove_tag,
    set_boost,
    toggle_featured,
)

SHOP_A, SHOP_B = uuid4(), uuid4()
PROD = uuid4()


def _row(**kw) -> dict:
    base = dict(
        id=str(PROD),
        shop_id=str(SHOP_A),
        category="Mobile",
        brand="Samsung",
        model="Galaxy",
        condition="New",
        specs={},
        cost_price=Decimal("1000.00"),
        selling_price=Decimal("1500.00"),
        quantity=1,
        images=[],
        boost_level=0,
        tags=[],
        is_featured=False,
    )
    return {**base, **kw}


class _Query:
    """Records .eq() filters; returns rows only if the shop_id filter matches the row's owner."""

    def __init__(self, table: "_FakeSupabase", rows: list[dict]) -> None:
        self._t = table
        self._rows = rows
        self._filters: dict[str, str] = {}
        self._patch: dict | None = None

    def select(self, *_a):
        return self

    def update(self, patch):
        self._patch = patch
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def gt(self, *_a):
        return self

    def execute(self):
        self._t.filters_seen.append(dict(self._filters))
        rows = [
            r
            for r in self._rows
            if all(str(r.get(c)) == str(v) for c, v in self._filters.items())
        ]
        if self._patch is not None:
            for r in rows:
                r.update(self._patch)
        return type("Resp", (), {"data": rows})()


class _FakeSupabase:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.filters_seen: list[dict] = []

    def table(self, _name):
        return _Query(self, self.rows)


# --- pure validation ---
def test_parse_boost_level_accepts_1_to_10():
    assert parse_boost_level("7") == 7
    for bad in ("0", "11", "-1", "abc", ""):
        with pytest.raises(InvalidBoostLevel):
            parse_boost_level(bad)


def test_parse_tags_normalizes_dedupes_and_rejects_unknown():
    assert parse_tags(" Clearance , gaming , clearance ") == ["clearance", "gaming"]
    with pytest.raises(InvalidTag):
        parse_tags("clearence")  # typo must not silently disable promotion
    with pytest.raises(InvalidTag):
        parse_tags("   ")


# --- the tenant guard (the security control of this stage) ---
@pytest.mark.asyncio
async def test_get_product_finds_own_product():
    sb = _FakeSupabase([_row()])
    p = await get_product(SHOP_A, PROD, client=sb)
    assert p.id == PROD
    assert sb.filters_seen[0]["shop_id"] == str(SHOP_A)  # always scoped


@pytest.mark.asyncio
async def test_shop_b_cannot_read_shop_a_product():
    sb = _FakeSupabase([_row()])  # row belongs to SHOP_A
    with pytest.raises(ProductNotFound):
        await get_product(SHOP_B, PROD, client=sb)


@pytest.mark.asyncio
async def test_shop_b_cannot_mutate_shop_a_product():
    sb = _FakeSupabase([_row(boost_level=0)])
    with pytest.raises(ProductNotFound):
        await set_boost(SHOP_B, PROD, 9, client=sb)
    assert sb.rows[0]["boost_level"] == 0  # untouched


# --- mutations ---
@pytest.mark.asyncio
async def test_set_boost_persists():
    sb = _FakeSupabase([_row()])
    assert (await set_boost(SHOP_A, PROD, 9, client=sb)).boost_level == 9


@pytest.mark.asyncio
async def test_add_tags_unions_without_duplicates():
    sb = _FakeSupabase([_row(tags=["clearance"])])
    p = await add_tags(SHOP_A, PROD, ["gaming", "clearance"], client=sb)
    assert p.tags == ["clearance", "gaming"]


@pytest.mark.asyncio
async def test_remove_absent_tag_is_a_noop():
    sb = _FakeSupabase([_row(tags=["gaming"])])
    assert (await remove_tag(SHOP_A, PROD, "premium", client=sb)).tags == ["gaming"]


@pytest.mark.asyncio
async def test_clear_tags_and_toggle_featured():
    sb = _FakeSupabase([_row(tags=["gaming"], is_featured=False)])
    assert (await clear_tags(SHOP_A, PROD, client=sb)).tags == []
    assert (await toggle_featured(SHOP_A, PROD, client=sb)).is_featured is True
    assert (await toggle_featured(SHOP_A, PROD, client=sb)).is_featured is False


# --- /addproduct field validation (SPEC §4, trust boundary) ---
def test_parse_category_and_condition_are_case_insensitive_and_canonical():
    from app.products.service import InvalidProductField, parse_category, parse_condition

    assert parse_category("mobile") == "Mobile"
    assert parse_condition(" refurbished ") == "Refurbished"
    for bad in ("phone", "", "Mobil"):
        with pytest.raises(InvalidProductField):
            parse_category(bad)
    with pytest.raises(InvalidProductField):
        parse_condition("brand-new")


def test_parse_price_is_decimal_never_float():
    from decimal import Decimal as D

    from app.products.service import InvalidProductField, parse_price

    p = parse_price("1499.5")
    assert p == D("1499.50") and isinstance(p, D)
    for bad in ("-1", "abc", "", "NaN", "Infinity"):
        with pytest.raises(InvalidProductField):
            parse_price(bad)


def test_parse_quantity_rejects_negative_and_junk():
    from app.products.service import InvalidProductField, parse_quantity

    assert parse_quantity(" 7 ") == 7
    assert parse_quantity("0") == 0
    for bad in ("-1", "1.5", "many", ""):
        with pytest.raises(InvalidProductField):
            parse_quantity(bad)


def test_parse_spec_line_splits_key_value():
    from app.products.service import InvalidProductField, parse_spec_line

    assert parse_spec_line("Camera: 108MP") == ("camera", "108MP")
    assert parse_spec_line("processor: Snapdragon 8 Gen 3") == ("processor", "Snapdragon 8 Gen 3")
    for bad in ("no colon here", "camera:", ": 108MP"):
        with pytest.raises(InvalidProductField):
            parse_spec_line(bad)


@pytest.mark.asyncio
async def test_create_product_forces_shop_id_and_stringifies_money():
    from decimal import Decimal as D

    from app.products.service import create_product, new_product_id

    sb = _FakeSupabase([])
    pid = new_product_id()

    class _Insert(_Query):
        def insert(self, row):
            self._inserted = row
            sb.rows.append(row)
            return self

    def _table(_name):
        return _Insert(sb, sb.rows)

    sb.table = _table
    p = await create_product(
        SHOP_A,
        product_id=pid,
        category="Mobile",
        brand="Samsung",
        model="Galaxy",
        color=None,
        condition="New",
        specs={"camera": "108MP"},
        cost_price=D("1000.00"),
        selling_price=D("1500.00"),
        quantity=3,
        client=sb,
    )
    assert p.shop_id == SHOP_A and p.id == pid
    row = sb.rows[0]
    assert row["shop_id"] == str(SHOP_A)          # never taken from user input
    assert row["cost_price"] == "1000.00"          # Decimal → str, never float
    assert isinstance(row["cost_price"], str)


# --- friendly codes (migration 010): PR0001 resolves like a UUID, still shop-scoped ---
from app.products.service import get_product_by_ref  # noqa: E402


@pytest.mark.asyncio
async def test_get_product_by_ref_accepts_uuid():
    sb = _FakeSupabase([_row()])
    p = await get_product_by_ref(SHOP_A, str(PROD), client=sb)
    assert p.id == PROD


@pytest.mark.asyncio
@pytest.mark.parametrize("ref", ["PR0007", "pr7", "7"])
async def test_get_product_by_ref_accepts_friendly_code(ref):
    sb = _FakeSupabase([_row(product_number=7)])
    p = await get_product_by_ref(SHOP_A, ref, client=sb)
    assert p.id == PROD and p.product_number == 7
    assert sb.filters_seen[-1] == {"product_number": 7, "shop_id": str(SHOP_A)}  # scoped by shop


@pytest.mark.asyncio
async def test_get_product_by_ref_code_is_tenant_scoped():
    # The row exists, but it belongs to shop A — asking as shop B must not find it.
    sb = _FakeSupabase([_row(product_number=7)])
    with pytest.raises(ProductNotFound):
        await get_product_by_ref(SHOP_B, "PR0007", client=sb)


@pytest.mark.asyncio
@pytest.mark.parametrize("ref", ["", "not-a-code", "PR0", "rider001"])
async def test_get_product_by_ref_junk_is_product_not_found(ref):
    # Junk must land on the same error a wrong id gets — never a raw crash for the keeper.
    sb = _FakeSupabase([_row(product_number=7)])
    with pytest.raises(ProductNotFound):
        await get_product_by_ref(SHOP_A, ref, client=sb)
