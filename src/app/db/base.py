"""Repository interface for the tenants domain (clients + shops + shopkeepers).

The tenants service depends on this abstract interface, not on any concrete
client. This lets us run and test the full tenant logic against an in-memory
repo (no Supabase credentials required) and swap in the Supabase-backed repo
once Q-003 (Supabase project) is resolved — without touching the service.

ADR-003 (RLS): concrete repos must scope all queries by shop_id.
ADR-006: adds the `clients` layer (one client -> many shops) + usage tracking.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from uuid import UUID

from app.tenants.models import Client, Shop, Shopkeeper, UsageDailyPoint


class TenantRepo(ABC):
    """Abstract tenant repository."""

    # --- clients (owner-level; ADR-006) ---
    @abstractmethod
    async def get_client(self, client_id: UUID) -> Client | None: ...

    @abstractmethod
    async def list_clients(self) -> list[Client]: ...

    @abstractmethod
    async def create_client(
        self,
        name: str,
        contact_name: str | None = None,
        contact_phone: str | None = None,
        email: str | None = None,
    ) -> Client: ...

    # --- shops ---
    @abstractmethod
    async def get_shop_by_id(self, shop_id: UUID) -> Shop | None: ...

    @abstractmethod
    async def get_shop_by_whatsapp_number(self, whatsapp_number: str) -> Shop | None:
        """Resolve a shop from a Twilio `To` number (SPEC §1)."""

    @abstractmethod
    async def list_shops(self, client_id: UUID | None = None) -> list[Shop]:
        """List shops, optionally scoped to a client (ADR-006)."""

    @abstractmethod
    async def suspend_shop(self, shop_id: UUID, reason: str) -> Shop:
        """Set shops.status = 'suspended' (SPEC §2)."""

    @abstractmethod
    async def resume_shop(self, shop_id: UUID) -> Shop:
        """Set shops.status = 'active' (SPEC §2)."""

    @abstractmethod
    async def create_shop(self, client_id: UUID, name: str, whatsapp_number: str | None = None) -> Shop:
        """Dev/test helper — create a shop under a client (ADR-006)."""

    # --- shopkeepers ---
    @abstractmethod
    async def get_shopkeeper_by_telegram_id(self, telegram_id: int) -> Shopkeeper | None:
        """Authenticate a Telegram user → shopkeeper (SPEC §1)."""

    @abstractmethod
    async def list_shopkeepers(self, shop_id: UUID) -> list[Shopkeeper]: ...

    @abstractmethod
    async def create_shopkeeper(
        self, shop_id: UUID, telegram_id: int, name: str | None = None, is_owner: bool = False
    ) -> Shopkeeper:
        """Dev/test helper — create a shopkeeper."""

    # --- usage (ADR-006; Stage 10 flush job writes these) ---
    @abstractmethod
    async def upsert_usage(self, client_id: UUID, shop_id: UUID, day: date, metric: str, count: int) -> None:
        """Upsert a daily usage aggregate (called by the Stage 10 flush job)."""

    @abstractmethod
    async def get_usage(self, client_id: UUID, day: date) -> list[UsageDailyPoint]:
        """Read usage for a client on a day (owner reports, Stage 8)."""

    # --- health (§13) ---
    @abstractmethod
    async def health_check(self) -> bool:
        """Cheap DB reachability probe for `/health` (SPEC §13). True if the DB answered."""
