"""Product model (SPEC §4). Columns mirror `products` in migrations/001_init.sql.

Money is Decimal, never float (CONVENTIONS).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class Product(BaseModel):
    id: UUID
    shop_id: UUID
    category: str  # Mobile | Laptop | Tablet | Accessory
    brand: str
    model: str
    color: str | None = None
    condition: str  # New | Used | Refurbished
    specs: dict[str, Any] = Field(default_factory=dict)  # flexible key:value (JSONB)
    cost_price: Decimal
    selling_price: Decimal
    quantity: int = 0
    images: list[str] = Field(default_factory=list)
    video_url: str | None = None
    boost_level: int = 0  # 0-10, internal only — never exposed to customers (SPEC §5)
    tags: list[str] = Field(default_factory=list)
    is_featured: bool = False
    product_number: int | None = None  # friendly ref "PR0001" (migration 010); null until backfilled
    min_qty: int = 0  # low-stock alert threshold; 0 = alerts off (migration 010)
    active_offer: str | None = None  # customer-facing offer label (migration 023); the AI may mention it
    created_at: datetime | None = None
