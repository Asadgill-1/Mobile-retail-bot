"""In-memory TenantRepo for tests and local dev without Supabase credentials.

Seeds itself from the same data as migrations/001_init.sql (incl. ADR-006
clients + multi-shop client) so tests mirror the real schema. Not for production.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from app.db.base import TenantRepo
from app.tenants.models import Client, ClientStatus, Shop, ShopStatus, Shopkeeper, UsageDailyPoint

# E.164 placeholders matching the SQL seed.
_SHOP1_NUMBER = "+10000000001"
_SHOP2_NUMBER = "+10000000002"
_SHOP3_NUMBER = "+10000000003"
_OWNER_TELEGRAM_ID = 100000001

_CLIENT_A = "Client A — TechStore Group"
_CLIENT_B = "Client B — Sharjah Mobiles"


class InMemoryTenantRepo(TenantRepo):
    def __init__(self) -> None:
        self._clients: dict[UUID, Client] = {}
        self._shops: dict[UUID, Shop] = {}
        self._shopkeepers: dict[int, Shopkeeper] = {}  # keyed by telegram_id
        self._by_number: dict[str, UUID] = {}
        self._usage: dict[tuple[UUID, UUID, date, str], int] = {}

    # --- seeding ---
    def seed_default(self) -> None:
        """Seed the dev clients + shops + owner shopkeeper (mirrors 001_init.sql).

        Per-shop Telegram bot tokens (ADR-005) are attached from settings when
        present, so the running system can poll the real shop bots. Tests that
        don't set TELEGRAM_SHOP_BOTS_JSON just get None tokens (harmless).
        """
        client_a = self.create_client_sync(_CLIENT_A, "Ahmed", "+971500000001", "ahmed@techstore.example")
        client_b = self.create_client_sync(_CLIENT_B, "Sara", "+971500000002", "sara@sharjahmobiles.example")

        s1 = self.create_shop_sync(client_a.id, "Shop 01 — Dubai Marina", _SHOP1_NUMBER)
        s2 = self.create_shop_sync(client_a.id, "Shop 02 — Abu Dhabi", _SHOP2_NUMBER)
        s3 = self.create_shop_sync(client_b.id, "Shop 03 — Sharjah", _SHOP3_NUMBER)
        # mark shop 3 suspended to exercise that path
        self._shops[s3.id] = s3.model_copy(
            update={"status": ShopStatus.SUSPENDED, "suspension_reason": "non-payment (seed)"}
        )
        self._attach_shop_bots([s1, s2])
        self.create_shopkeeper_sync(s1.id, _OWNER_TELEGRAM_ID, "Owner", is_owner=True)

    def _attach_shop_bots(self, shops: list[Shop]) -> None:
        """Attach per-shop bot tokens from settings (positional; testing only)."""
        from app.core.config import settings

        bots = settings.shop_bots
        if not bots:
            return
        for shop, cfg in zip(shops, bots):
            self._shops[shop.id] = shop.model_copy(
                update={
                    "telegram_keeper_bot_token": cfg.get("keeper_token"),
                    "telegram_customer_bot_token": cfg.get("customer_token"),
                    "telegram_customer_chat_id": cfg.get("customer_chat_id"),
                }
            )

    # --- internal sync helpers (so seeding/tests don't need await) ---
    def create_client_sync(
        self,
        name: str,
        contact_name: str | None = None,
        contact_phone: str | None = None,
        email: str | None = None,
    ) -> Client:
        client = Client(
            id=uuid4(), name=name, contact_name=contact_name, contact_phone=contact_phone, email=email
        )
        self._clients[client.id] = client
        return client

    def create_shop_sync(self, client_id: UUID, name: str, whatsapp_number: str | None = None) -> Shop:
        shop = Shop(id=uuid4(), client_id=client_id, name=name, whatsapp_number=whatsapp_number)
        self._shops[shop.id] = shop
        if whatsapp_number:
            self._by_number[whatsapp_number] = shop.id
        return shop

    def create_shopkeeper_sync(
        self, shop_id: UUID, telegram_id: int, name: str | None = None, is_owner: bool = False
    ) -> Shopkeeper:
        sk = Shopkeeper(id=uuid4(), shop_id=shop_id, telegram_id=telegram_id, name=name, is_owner=is_owner)
        self._shopkeepers[telegram_id] = sk
        return sk

    # --- clients ---
    async def get_client(self, client_id: UUID) -> Client | None:
        return self._clients.get(client_id)

    async def list_clients(self) -> list[Client]:
        return list(self._clients.values())

    async def create_client(
        self,
        name: str,
        contact_name: str | None = None,
        contact_phone: str | None = None,
        email: str | None = None,
    ) -> Client:
        return self.create_client_sync(name, contact_name, contact_phone, email)

    # --- shops ---
    async def get_shop_by_id(self, shop_id: UUID) -> Shop | None:
        return self._shops.get(shop_id)

    async def get_shop_by_whatsapp_number(self, whatsapp_number: str) -> Shop | None:
        shop_id = self._by_number.get(whatsapp_number)
        return self._shops.get(shop_id) if shop_id else None

    async def list_shops(self, client_id: UUID | None = None) -> list[Shop]:
        if client_id is None:
            return list(self._shops.values())
        return [s for s in self._shops.values() if s.client_id == client_id]

    async def suspend_shop(self, shop_id: UUID, reason: str) -> Shop:
        shop = self._shops.get(shop_id)
        if shop is None:
            raise KeyError(f"shop {shop_id} not found")
        updated = shop.model_copy(update={"status": ShopStatus.SUSPENDED, "suspension_reason": reason})
        self._shops[shop_id] = updated
        return updated

    async def resume_shop(self, shop_id: UUID) -> Shop:
        shop = self._shops.get(shop_id)
        if shop is None:
            raise KeyError(f"shop {shop_id} not found")
        updated = shop.model_copy(update={"status": ShopStatus.ACTIVE, "suspension_reason": None})
        self._shops[shop_id] = updated
        return updated

    async def create_shop(self, client_id: UUID, name: str, whatsapp_number: str | None = None) -> Shop:
        return self.create_shop_sync(client_id, name, whatsapp_number)

    # --- shopkeepers ---
    async def get_shopkeeper_by_telegram_id(self, telegram_id: int) -> Shopkeeper | None:
        return self._shopkeepers.get(telegram_id)

    async def list_shopkeepers(self, shop_id: UUID) -> list[Shopkeeper]:
        return [sk for sk in self._shopkeepers.values() if sk.shop_id == shop_id]

    async def create_shopkeeper(
        self, shop_id: UUID, telegram_id: int, name: str | None = None, is_owner: bool = False
    ) -> Shopkeeper:
        return self.create_shopkeeper_sync(shop_id, telegram_id, name, is_owner)

    # --- usage (ADR-006) ---
    async def upsert_usage(
        self, client_id: UUID, shop_id: UUID, day: date, metric: str, count: int
    ) -> None:
        self._usage[(client_id, shop_id, day, metric)] = count

    async def get_usage(self, client_id: UUID, day: date) -> list[UsageDailyPoint]:
        return [
            UsageDailyPoint(client_id=cid, shop_id=sid, day=d, metric=m, count=cnt)
            for (cid, sid, d, m), cnt in self._usage.items()
            if cid == client_id and d == day
        ]

    async def health_check(self) -> bool:
        return True  # in-memory is always reachable
