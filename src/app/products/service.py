"""Product mutations: boost, tags, featured (SPEC §5). Owned by `products/`.

Every mutation resolves the product through `get_product(shop_id, ...)` first, so a
shopkeeper can never touch another shop's row by passing its UUID. Tenant isolation is
sacred (ARCHITECTURE §1) — and RLS is still a permissive scaffold, so the app layer is
the only thing enforcing it today.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

from app.products.models import Product

logger = logging.getLogger(__name__)

# SPEC §4 — DB CHECK constraints mirror these exactly (migrations/001_init.sql).
VALID_CATEGORIES: tuple[str, ...] = ("Mobile", "Laptop", "Tablet", "Accessory")
VALID_CONDITIONS: tuple[str, ...] = ("New", "Used", "Refurbished")

# SPEC §5 — the tag vocabulary the AI's promotion prompt understands.
# A typo ("clearence") would silently never promote, so the set is enforced, not suggested.
VALID_TAGS: frozenset[str] = frozenset(
    {
        "clearance",
        "trending",
        "best_camera",
        "long_battery",
        "gaming",
        "budget",
        "premium",
        "high_margin",
        "staff_pick",
        "new_arrival",
        "limited_stock",
    }
)

MIN_BOOST, MAX_BOOST = 1, 10


class ProductNotFound(Exception):
    """Unknown product id, or it belongs to a different shop. Same message either way —
    never confirm that another shop's product exists."""


class InvalidBoostLevel(Exception):
    pass


class InvalidTag(Exception):
    pass


class InvalidProductField(Exception):
    """A field of the /addproduct draft failed validation (SPEC §4)."""


# --- pure validation (trust boundary: shopkeeper free text) ---
def parse_category(raw: str) -> str:
    """Case-insensitive → canonical. Must match the DB CHECK constraint."""
    for c in VALID_CATEGORIES:
        if c.lower() == (raw or "").strip().lower():
            return c
    raise InvalidProductField(f"category must be one of: {', '.join(VALID_CATEGORIES)}")


def parse_condition(raw: str) -> str:
    for c in VALID_CONDITIONS:
        if c.lower() == (raw or "").strip().lower():
            return c
    raise InvalidProductField(f"condition must be one of: {', '.join(VALID_CONDITIONS)}")


def parse_price(raw: str) -> Decimal:
    """Money is Decimal, never float (CONVENTIONS). Rejects negatives and junk."""
    try:
        price = Decimal((raw or "").strip())
    except InvalidOperation:
        raise InvalidProductField(f"price must be a number, got {raw!r}")
    if price.is_nan() or price.is_infinite() or price < 0:
        raise InvalidProductField(f"price must be >= 0, got {raw!r}")
    return price.quantize(Decimal("0.01"))


def parse_quantity(raw: str) -> int:
    try:
        qty = int((raw or "").strip())
    except (TypeError, ValueError):
        raise InvalidProductField(f"quantity must be a whole number, got {raw!r}")
    if qty < 0:
        raise InvalidProductField("quantity must be >= 0")
    return qty


def parse_spec_line(line: str) -> tuple[str, str]:
    """`camera: 108MP` → ("camera", "108MP") (SPEC §4 step 6)."""
    key, sep, value = (line or "").partition(":")
    if not sep or not key.strip() or not value.strip():
        raise InvalidProductField(f"specs must be `key: value`, got {line!r}")
    return key.strip().lower(), value.strip()


def parse_non_empty(raw: str, field: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise InvalidProductField(f"{field} cannot be empty")
    return text
def parse_boost_level(raw: str) -> int:
    """`/boost <id> <level>` → 1..10 (SPEC §5)."""
    try:
        level = int(raw)
    except (TypeError, ValueError):
        raise InvalidBoostLevel(f"boost level must be a number {MIN_BOOST}-{MAX_BOOST}, got {raw!r}")
    if not MIN_BOOST <= level <= MAX_BOOST:
        raise InvalidBoostLevel(f"boost level must be {MIN_BOOST}-{MAX_BOOST}, got {level}")
    return level


def parse_tags(raw: str) -> list[str]:
    """`/tag <id> <tag1,tag2>` → normalized, de-duplicated, whitelist-checked (SPEC §5)."""
    tags = [t.strip().lower() for t in (raw or "").split(",") if t.strip()]
    if not tags:
        raise InvalidTag("no tags given")
    unknown = [t for t in tags if t not in VALID_TAGS]
    if unknown:
        raise InvalidTag(f"unknown tag(s): {', '.join(unknown)}. Valid: {', '.join(sorted(VALID_TAGS))}")
    return list(dict.fromkeys(tags))  # de-dupe, preserve order


# --- DB access (supabase-py is sync → off the event loop) ---
def _sb(client: Any | None) -> Any:
    from app.db.supabase_client import get_supabase

    return client if client is not None else get_supabase()


def new_product_id() -> UUID:
    """Client-side id so media can be uploaded to `{shop_id}/{product_id}/` before the insert."""
    return uuid4()


async def create_product(
    shop_id: UUID,
    *,
    product_id: UUID,
    category: str,
    brand: str,
    model: str,
    color: str | None,
    condition: str,
    specs: dict[str, str],
    cost_price: Decimal,
    selling_price: Decimal,
    quantity: int,
    images: list[str] | None = None,
    video_url: str | None = None,
    client: Any | None = None,
) -> Product:
    """Insert a product (SPEC §4 step 11). `shop_id` comes from the bot's shop, never user input."""
    sb = _sb(client)
    row = {
        "id": str(product_id),
        "shop_id": str(shop_id),
        "category": category,
        "brand": brand,
        "model": model,
        "color": color,
        "condition": condition,
        "specs": specs,
        "cost_price": str(cost_price),  # Decimal → string; never float across the wire
        "selling_price": str(selling_price),
        "quantity": quantity,
        "images": images or [],
        "video_url": video_url,
    }

    def _q() -> Product:
        r = sb.table("products").insert(row).execute()
        return Product(**r.data[0])

    return await asyncio.to_thread(_q)


async def get_product(shop_id: UUID, product_id: UUID, client: Any | None = None) -> Product:
    """Resolve a product **within this shop**. Raises ProductNotFound otherwise.

    THE tenant guard: every mutation below goes through here first.
    """
    sb = _sb(client)

    def _q() -> Product | None:
        r = (
            sb.table("products")
            .select("*")
            .eq("id", str(product_id))
            .eq("shop_id", str(shop_id))  # cross-shop access impossible
            .execute()
        )
        rows = r.data or []
        return Product(**rows[0]) if rows else None

    product = await asyncio.to_thread(_q)
    if product is None:
        raise ProductNotFound(str(product_id))
    return product


async def get_product_by_ref(shop_id: UUID, ref: str, client: Any | None = None) -> Product:
    """Resolve a product the shopkeeper named — either a full UUID or a friendly code ('PR0001').

    UUID → the existing tenant guard. Otherwise parse the code to a product_number and look it up
    within this shop. Both paths raise ProductNotFound on miss (same message — never confirm a
    foreign product exists)."""
    from app.utils.codes import parse_product_code

    ref = (ref or "").strip()
    try:
        return await get_product(shop_id, UUID(ref), client)
    except ValueError:
        pass  # not a UUID — try a friendly code

    number = parse_product_code(ref)
    if number is None:
        raise ProductNotFound(ref)

    sb = _sb(client)

    def _q() -> Product | None:
        r = (
            sb.table("products")
            .select("*")
            .eq("product_number", number)
            .eq("shop_id", str(shop_id))  # cross-shop access impossible
            .execute()
        )
        rows = r.data or []
        return Product(**rows[0]) if rows else None

    product = await asyncio.to_thread(_q)
    if product is None:
        raise ProductNotFound(ref)
    return product


async def _update(shop_id: UUID, product_id: UUID, patch: dict[str, Any], client: Any | None) -> Product:
    """Apply a patch to a product already proven to belong to this shop."""
    sb = _sb(client)

    def _q() -> Product:
        r = (
            sb.table("products")
            .update(patch)
            .eq("id", str(product_id))
            .eq("shop_id", str(shop_id))  # belt-and-braces: RLS is still permissive
            .execute()
        )
        return Product(**r.data[0])

    return await asyncio.to_thread(_q)


async def set_boost(shop_id: UUID, product_id: UUID, level: int, client: Any | None = None) -> Product:
    """`/boost` — set boost 1-10. `/unboost` passes 0 (SPEC §5)."""
    await get_product(shop_id, product_id, client)
    return await _update(shop_id, product_id, {"boost_level": level}, client)


async def add_tags(shop_id: UUID, product_id: UUID, tags: list[str], client: Any | None = None) -> Product:
    """`/tag` — union of existing + new tags."""
    product = await get_product(shop_id, product_id, client)
    merged = list(dict.fromkeys([*product.tags, *tags]))
    return await _update(shop_id, product_id, {"tags": merged}, client)


async def remove_tag(shop_id: UUID, product_id: UUID, tag: str, client: Any | None = None) -> Product:
    """`/untag` — remove one tag. Removing an absent tag is a no-op, not an error."""
    product = await get_product(shop_id, product_id, client)
    remaining = [t for t in product.tags if t != tag.strip().lower()]
    return await _update(shop_id, product_id, {"tags": remaining}, client)


async def clear_tags(shop_id: UUID, product_id: UUID, client: Any | None = None) -> Product:
    """`/cleartags` — remove all tags."""
    await get_product(shop_id, product_id, client)
    return await _update(shop_id, product_id, {"tags": []}, client)


async def toggle_featured(shop_id: UUID, product_id: UUID, client: Any | None = None) -> Product:
    """`/feature` — toggle is_featured (SPEC §5)."""
    product = await get_product(shop_id, product_id, client)
    return await _update(shop_id, product_id, {"is_featured": not product.is_featured}, client)


async def list_inventory(shop_id: UUID, client: Any | None = None) -> list[dict]:
    """Stock list for the shop-owner bot's 🗃 Inventory button — low stock first."""
    sb = _sb(client)

    def _q() -> list[dict]:
        return (
            sb.table("products")
            .select("product_number,brand,model,color,quantity,selling_price,cost_price,min_qty")
            .eq("shop_id", str(shop_id))
            .order("quantity")
            .limit(100)
            .execute()
            .data
            or []
        )

    return await asyncio.to_thread(_q)
