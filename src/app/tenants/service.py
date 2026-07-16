"""Tenant service — clients + shop lifecycle + suspension (SPEC §1, §2; ADR-006).

Business logic lives here; it depends on the TenantRepo interface (db/base.py),
never on a concrete client. Owner-auth checks are in tenants/auth.py.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from app.db.base import TenantRepo
from app.tenants.models import Client, Shop, Shopkeeper, ShopStatus, ShopStatusInfo, UsageDailyPoint


class ShopNotFound(Exception):
    pass


class ClientNotFound(Exception):
    pass


class TenantService:
    def __init__(self, repo: TenantRepo) -> None:
        self._repo = repo

    # --- clients (owner-level; ADR-006) ---
    async def list_clients(self) -> list[Client]:
        return await self._repo.list_clients()

    async def get_client(self, client_id: UUID) -> Client:
        c = await self._repo.get_client(client_id)
        if c is None:
            raise ClientNotFound(str(client_id))
        return c

    async def list_shops_by_client(self, client_id: UUID) -> list[Shop]:
        """All shops belonging to a client (multi-shop clients, ADR-006)."""
        await self.get_client(client_id)  # raises if unknown
        return await self._repo.list_shops(client_id=client_id)

    async def client_by_telegram(self, telegram_id: int) -> Client | None:
        """Shop-owner bot auth: resolve a Telegram user to their client (None = not linked)."""
        return await self._repo.get_client_by_telegram_id(telegram_id)

    async def link_client_telegram(self, phone: str, telegram_id: int) -> list[Client]:
        """Shop-owner bot linking: stamp telegram_id on clients matching this phone."""
        return await self._repo.link_client_telegram(phone, telegram_id)

    async def get_usage(self, client_id: UUID, day: date) -> list[UsageDailyPoint]:
        """Owner usage/billing insight for a client on a day (ADR-006)."""
        return await self._repo.get_usage(client_id, day)

    # --- onboarding (platform owner only; the repos always had these, nothing could reach them) ---
    async def create_client(
        self, name: str, contact_name: str | None = None,
        contact_phone: str | None = None, email: str | None = None,
    ) -> Client:
        """Onboard a new client (the person who owns shops)."""
        if not name or not name.strip():
            raise ValueError("client name is required")
        return await self._repo.create_client(
            name.strip(), contact_name=contact_name, contact_phone=contact_phone, email=email
        )

    async def create_shop(
        self, client_id: UUID, name: str, whatsapp_number: str | None = None
    ) -> Shop:
        """Onboard a shop under an existing client."""
        if not name or not name.strip():
            raise ValueError("shop name is required")
        await self.get_client(client_id)  # raises ClientNotFound — never orphan a shop
        return await self._repo.create_shop(client_id, name.strip(), whatsapp_number)

    async def create_shopkeeper(
        self, shop_id: UUID, telegram_id: int, name: str | None = None, is_owner: bool = False
    ) -> Shopkeeper:
        """Give a person access to a shop's keeper bot."""
        await self.get_shop(shop_id)  # raises ShopNotFound
        return await self._repo.create_shopkeeper(shop_id, telegram_id, name, is_owner)

    async def set_shop_tokens(
        self, shop_id: UUID, keeper_token: str | None = None, customer_token: str | None = None
    ) -> Shop:
        """Attach the shop's bot tokens. None = leave that one as it is."""
        await self.get_shop(shop_id)  # raises ShopNotFound
        return await self._repo.update_shop_tokens(shop_id, keeper_token, customer_token)

    # --- shops ---
    async def get_shop_by_whatsapp_number(self, number: str) -> Shop | None:
        """Resolve shop from a Twilio `To` number (SPEC §1). None if unknown number."""
        return await self._repo.get_shop_by_whatsapp_number(number)

    async def get_shop(self, shop_id: UUID) -> Shop:
        shop = await self._repo.get_shop_by_id(shop_id)
        if shop is None:
            raise ShopNotFound(str(shop_id))
        return shop

    async def list_shops(self, client_id: UUID | None = None) -> list[Shop]:
        return await self._repo.list_shops(client_id=client_id)

    async def suspend_shop(self, shop_id: UUID, reason: str) -> Shop:
        """Owner-only: set shops.status='suspended' with a reason (SPEC §2)."""
        if not reason or not reason.strip():
            raise ValueError("suspension reason is required")
        await self.get_shop(shop_id)  # raises ShopNotFound if missing
        return await self._repo.suspend_shop(shop_id, reason.strip())

    async def resume_shop(self, shop_id: UUID) -> Shop:
        """Owner-only: set shops.status='active' (SPEC §2)."""
        await self.get_shop(shop_id)
        return await self._repo.resume_shop(shop_id)

    async def shop_status(self, shop_id: UUID) -> ShopStatusInfo:
        """Response shape for `/shopstatus` (SPEC §2)."""
        shop = await self.get_shop(shop_id)
        shopkeepers = await self._repo.list_shopkeepers(shop_id)
        return ShopStatusInfo(
            shop_id=shop.id,
            client_id=shop.client_id,
            name=shop.name,
            status=shop.status,
            suspension_reason=shop.suspension_reason,
            shopkeepers=shopkeepers,
        )

    async def is_suspended(self, shop_id: UUID) -> bool:
        """Convenience for the message pipeline (SPEC §9 step 2)."""
        shop = await self.get_shop(shop_id)
        return shop.status == ShopStatus.SUSPENDED
