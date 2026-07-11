"""Product search + boost-aware ranking (SPEC §4). Owned by `products/`, called by `ai/`.

Relevance ranking (SPEC §4):
    relevance = matching spec pairs + (matching tags x 2)
    score     = relevance * (1 + boost_level / 10)
    order     = score DESC, is_featured DESC, boost_level DESC

The trailing keys implement SPEC §5 "vague requests: prioritize featured products":
when a request matches nothing, every score is 0 and featured items surface first.

Price ordering (Q-015, ADR-008 rev.2):
A relevance-ranked top-N slice cannot answer a superlative. "Cheapest" asked of a
boost-ranked list returns whatever the shop promoted, not the cheapest thing — the model
then states it as fact, and that reads as a hallucination even though the model invented
nothing. So `sort="price_asc"|"price_desc"` orders by price and **ignores boost entirely**:
a promoted product must never be able to hide a cheaper one. Boost promotes; it does not lie.
"""

from __future__ import annotations

import asyncio
import logging
import re
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from app.products.models import Product

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_DEFAULT_LIMIT = 5

SortOrder = Literal["relevance", "price_asc", "price_desc"]

# Customers don't speak the schema. "phone" is never in a `Mobile` row; "cheap" is never in a
# `budget` tag. Without these, such queries score 0 relevance everywhere and collapse to boost
# order — which is exactly how the cheapest phone became invisible (Q-015).
_SYNONYMS: dict[str, str] = {
    # category words -> the DB's category value
    "phone": "mobile",
    "phones": "mobile",
    "smartphone": "mobile",
    "smartphones": "mobile",
    "mobiles": "mobile",
    "handset": "mobile",
    "notebook": "laptop",
    "laptops": "laptop",
    "tablets": "tablet",
    "ipad": "tablet",
    "accessories": "accessory",
    # intent words -> the SPEC §5 tag that encodes them
    "cheap": "budget",
    "cheapest": "budget",
    "cheaper": "budget",
    "affordable": "budget",
    "inexpensive": "budget",
    "sale": "clearance",
    "discount": "clearance",
    "deal": "clearance",
    "popular": "trending",
}


def _tokens(text: str) -> set[str]:
    """Tokenize, then add schema-side synonyms for the words customers actually use."""
    found = set(_TOKEN_RE.findall(text.lower()))
    return found | {_SYNONYMS[t] for t in found if t in _SYNONYMS}


def _spec_text(p: Product) -> str:
    """Text a spec token can match against.

    SPEC §4 says "specs JSONB ILIKE"; brand/model/category/colour/condition are included
    too because a customer asking for "Samsung phone" is searching those fields in practice.
    """
    parts = [p.brand, p.model, p.category, p.color or "", p.condition]
    parts += [f"{k} {v}" for k, v in p.specs.items()]
    return " ".join(parts).lower()


def relevance(p: Product, tokens: set[str]) -> int:
    """Matching spec pairs + matching tags x 2 (SPEC §4)."""
    spec_text = _spec_text(p)
    spec_matches = sum(1 for t in tokens if t in spec_text)
    tag_text = " ".join(p.tags).lower()
    tag_matches = sum(1 for t in tokens if t in tag_text)
    return spec_matches + 2 * tag_matches


def score(p: Product, tokens: set[str]) -> float:
    """Relevance multiplied by (1 + boost_level/10) (SPEC §4)."""
    return relevance(p, tokens) * (1 + p.boost_level / 10)


def rank(
    products: list[Product],
    requirements: str,
    limit: int = _DEFAULT_LIMIT,
    *,
    max_price: Decimal | None = None,
    sort: SortOrder = "relevance",
) -> list[Product]:
    """Order products for one customer request. Pure — the whole algorithm lives here."""
    tokens = _tokens(requirements)
    pool = [p for p in products if max_price is None or p.selling_price <= max_price]

    if sort == "relevance":
        pool.sort(key=lambda p: (score(p, tokens), p.is_featured, p.boost_level), reverse=True)
        return pool[:limit]

    # Price ordering. Narrow to what the customer actually described (if anything matched),
    # then order strictly by price. Boost is deliberately absent: it must not hide a cheaper item.
    matched = [p for p in pool if relevance(p, tokens) > 0]
    candidates = matched or pool  # "cheapest phone" with no spec match still means every phone
    candidates.sort(key=lambda p: p.selling_price, reverse=(sort == "price_desc"))
    return candidates[:limit]


async def search_products(
    shop_id: UUID,
    requirements: str,
    limit: int = _DEFAULT_LIMIT,
    *,
    max_price: Decimal | float | str | None = None,
    sort: SortOrder = "relevance",
    client: Any | None = None,
) -> list[Product]:
    """Fetch this shop's in-stock products and rank them (SPEC §4). Tenant-scoped by shop_id."""
    from app.db.supabase_client import get_supabase

    sb = client if client is not None else get_supabase()
    cap = Decimal(str(max_price)) if max_price is not None else None  # never compare against float

    # supabase-py is sync; run it off the event loop or every bot stalls (see SupabaseTenantRepo).
    # ponytail: fetch the shop's in-stock rows, rank in Python. ceiling: O(catalogue) per message.
    # upgrade: a Postgres RPC using the existing GIN indexes on specs/tags once a shop
    # outgrows a few hundred products.
    def _q() -> list[Product]:
        resp = (
            sb.table("products")
            .select("*")
            .eq("shop_id", str(shop_id))
            .gt("quantity", 0)  # never offer out-of-stock items
            .execute()
        )
        return [Product(**row) for row in (resp.data or [])]

    return rank(await asyncio.to_thread(_q), requirements, limit, max_price=cap, sort=sort)
