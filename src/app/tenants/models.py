"""Pydantic models for the tenants domain (clients + shops + shopkeepers).

Source: docs/SPEC-source.md §1, §2. ADR-006 adds the `clients` layer above shops.
DB columns in migrations/001_init.sql.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class ClientStatus(StrEnum):
    ACTIVE = "active"
    OFFBOARDED = "offboarded"


class ShopStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class Client(BaseModel):
    """A service-provider client business; may own multiple shops (ADR-006)."""

    id: UUID
    name: str
    contact_name: str | None = None
    contact_phone: str | None = None
    email: str | None = None
    status: ClientStatus = ClientStatus.ACTIVE
    # Linked on the global shop-owner bot via contact share (migration 009; mirrors riders).
    telegram_id: int | None = None
    created_at: datetime | None = None


class Shop(BaseModel):
    id: UUID
    client_id: UUID
    name: str
    whatsapp_number: str | None = None
    status: ShopStatus = ShopStatus.ACTIVE
    suspension_reason: str | None = None
    negotiation_enabled: bool = True  # ADR-010 rev.: shop lets the AI raise price requests (else no discounts)
    # ADR-005: per-shop Telegram bots. keeper = shopkeeper-side staff bot;
    # customer = customer-facing bot (the "WhatsApp" channel in Telegram-first testing).
    # Persisted on live shops rows (migrations/001_init.sql) and mapped by
    # SupabaseTenantRepo._row_to_shop; seeded via scripts/seed_shop_bots.py.
    telegram_keeper_bot_token: str | None = None
    telegram_customer_bot_token: str | None = None
    telegram_customer_chat_id: int | None = None  # test chat id (userbot side)
    created_at: datetime | None = None


class Shopkeeper(BaseModel):
    id: UUID
    shop_id: UUID
    telegram_id: int
    name: str | None = None
    is_owner: bool = False
    created_at: datetime | None = None


class ShopStatusInfo(BaseModel):
    """Response shape for `/shopstatus` (SPEC §2)."""

    shop_id: UUID
    client_id: UUID
    name: str
    status: ShopStatus
    suspension_reason: str | None = None
    shopkeepers: list[Shopkeeper] = Field(default_factory=list)


class UsageDailyPoint(BaseModel):
    """One row of usage_daily (ADR-006). For owner usage/billing reports."""

    client_id: UUID
    shop_id: UUID
    day: date
    metric: str
    count: int
