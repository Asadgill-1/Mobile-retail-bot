"""Supabase client factory + Supabase-backed TenantRepo.

Real implementation. Exercised once Supabase credentials are provided (Q-003).
Until then, tests and local dev use InMemoryTenantRepo.

ADR-003: the backend uses the SERVICE-ROLE key (RLS bypass) for trusted
cross-shop operations (owner reports, health) AND the application layer still
scopes every query by shop_id. The two layers are complementary.
ADR-006: clients + usage_daily.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.db.base import TenantRepo
from app.tenants.models import Client, ClientStatus, Shop, ShopStatus, Shopkeeper, UsageDailyPoint

_client: Any = None


def get_supabase() -> Any:
    """Return a cached supabase.Client (created from settings). Raises if unconfigured."""
    global _client
    if _client is None:
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise RuntimeError(
                "Supabase not configured. Set SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY, "
                "or use InMemoryTenantRepo for local dev/tests (Q-003)."
            )
        from supabase import create_client  # local import to avoid hard dep at import time

        _client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    return _client


def _row_to_client(row: dict) -> Client:
    return Client(
        id=row["id"],
        name=row["name"],
        contact_name=row.get("contact_name"),
        contact_phone=row.get("contact_phone"),
        email=row.get("email"),
        status=ClientStatus(row.get("status", "active")),
        telegram_id=row.get("telegram_id"),
        created_at=row.get("created_at"),
    )


def _row_to_shop(row: dict) -> Shop:
    return Shop(
        id=row["id"],
        client_id=row["client_id"],
        name=row["name"],
        whatsapp_number=row.get("whatsapp_number"),
        status=ShopStatus(row.get("status", "active")),
        suspension_reason=row.get("suspension_reason"),
        negotiation_enabled=row.get("negotiation_enabled", True),
        # ADR-005 per-shop bot tokens (persisted on the live shops rows).
        telegram_keeper_bot_token=row.get("telegram_keeper_bot_token"),
        telegram_customer_bot_token=row.get("telegram_customer_bot_token"),
        telegram_customer_chat_id=row.get("telegram_customer_chat_id"),
        created_at=row.get("created_at"),
    )


def _row_to_shopkeeper(row: dict) -> Shopkeeper:
    return Shopkeeper(
        id=row["id"],
        shop_id=row["shop_id"],
        telegram_id=row["telegram_id"],
        name=row.get("name"),
        is_owner=row.get("is_owner", False),
        created_at=row.get("created_at"),
    )


class SupabaseTenantRepo(TenantRepo):
    """Supabase-backed tenant repository. Uses asyncio.to_thread around the
    sync supabase-py SDK so the service stays fully async."""  # Exercised against live project uwlczgwlkqlflpveeykj (2026-07-07): list_shops reads 3 seeded shops. Q-003 resolved.

    def __init__(self, client: Any | None = None) -> None:
        self._client = client  # inject for tests; defaults to get_supabase()

    @property
    def sb(self) -> Any:
        return self._client if self._client is not None else get_supabase()

    # --- clients ---
    async def get_client(self, client_id: UUID) -> Client | None:
        def _q() -> Client | None:
            r = self.sb.table("clients").select("*").eq("id", str(client_id)).maybe_single().execute()
            return _row_to_client(r.data) if r and r.data else None

        return await asyncio.to_thread(_q)

    async def list_clients(self) -> list[Client]:
        def _q() -> list[Client]:
            r = self.sb.table("clients").select("*").execute()
            return [_row_to_client(row) for row in (r.data or [])]

        return await asyncio.to_thread(_q)

    async def create_client(
        self,
        name: str,
        contact_name: str | None = None,
        contact_phone: str | None = None,
        email: str | None = None,
    ) -> Client:
        def _q() -> Client:
            payload: dict[str, Any] = {"name": name}
            if contact_name:
                payload["contact_name"] = contact_name
            if contact_phone:
                payload["contact_phone"] = contact_phone
            if email:
                payload["email"] = email
            r = self.sb.table("clients").insert(payload).execute()
            return _row_to_client(r.data[0])

        return await asyncio.to_thread(_q)

    async def get_client_by_telegram_id(self, telegram_id: int) -> Client | None:
        def _q() -> Client | None:
            # ponytail: first match; a person owning two client rows gets the first — upgrade if real.
            r = self.sb.table("clients").select("*").eq("telegram_id", telegram_id).limit(1).execute()
            return _row_to_client(r.data[0]) if r.data else None

        return await asyncio.to_thread(_q)

    async def link_client_telegram(self, phone: str, telegram_id: int) -> list[Client]:
        from app.riders.service import _normalize_phone  # same UAE rule as rider linking

        def _q() -> list[Client]:
            target = _normalize_phone(phone)
            if not target:
                return []
            # Python-side phone match over ~30 clients (formats vary), same as rider link_telegram.
            rows = self.sb.table("clients").select("*").execute().data or []
            matched = [
                row
                for row in rows
                if row.get("contact_phone") and _normalize_phone(row["contact_phone"]) == target
            ]
            out: list[Client] = []
            for row in matched:
                r = (
                    self.sb.table("clients")
                    .update({"telegram_id": telegram_id})
                    .eq("id", row["id"])
                    .execute()
                )
                out.append(_row_to_client(r.data[0] if r.data else {**row, "telegram_id": telegram_id}))
            return out

        return await asyncio.to_thread(_q)

    # --- shops ---
    async def get_shop_by_id(self, shop_id: UUID) -> Shop | None:
        def _q() -> Shop | None:
            r = self.sb.table("shops").select("*").eq("id", str(shop_id)).maybe_single().execute()
            return _row_to_shop(r.data) if r and r.data else None

        return await asyncio.to_thread(_q)

    async def get_shop_by_whatsapp_number(self, whatsapp_number: str) -> Shop | None:
        def _q() -> Shop | None:
            r = (
                self.sb.table("shops")
                .select("*")
                .eq("whatsapp_number", whatsapp_number)
                .maybe_single()
                .execute()
            )
            return _row_to_shop(r.data) if r and r.data else None

        return await asyncio.to_thread(_q)

    async def list_shops(self, client_id: UUID | None = None) -> list[Shop]:
        def _q() -> list[Shop]:
            q = self.sb.table("shops").select("*")
            if client_id is not None:
                q = q.eq("client_id", str(client_id))
            r = q.execute()
            return [_row_to_shop(row) for row in (r.data or [])]

        return await asyncio.to_thread(_q)

    async def suspend_shop(self, shop_id: UUID, reason: str) -> Shop:
        def _q() -> Shop:
            r = (
                self.sb.table("shops")
                .update({"status": "suspended", "suspension_reason": reason})
                .eq("id", str(shop_id))
                .execute()
            )
            if not r.data:
                raise KeyError(f"shop {shop_id} not found")
            return _row_to_shop(r.data[0])

        return await asyncio.to_thread(_q)

    async def resume_shop(self, shop_id: UUID) -> Shop:
        def _q() -> Shop:
            r = (
                self.sb.table("shops")
                .update({"status": "active", "suspension_reason": None})
                .eq("id", str(shop_id))
                .execute()
            )
            if not r.data:
                raise KeyError(f"shop {shop_id} not found")
            return _row_to_shop(r.data[0])

        return await asyncio.to_thread(_q)

    async def create_shop(self, client_id: UUID, name: str, whatsapp_number: str | None = None) -> Shop:
        def _q() -> Shop:
            payload: dict[str, Any] = {"client_id": str(client_id), "name": name}
            if whatsapp_number:
                payload["whatsapp_number"] = whatsapp_number
            r = self.sb.table("shops").insert(payload).execute()
            return _row_to_shop(r.data[0])

        return await asyncio.to_thread(_q)

    async def update_shop_tokens(
        self, shop_id: UUID, keeper_token: str | None = None, customer_token: str | None = None
    ) -> Shop:
        def _q() -> Shop:
            patch: dict[str, Any] = {}
            if keeper_token is not None:
                patch["telegram_keeper_bot_token"] = keeper_token
            if customer_token is not None:
                patch["telegram_customer_bot_token"] = customer_token
            if not patch:  # nothing to change — read the row back rather than issue a bare update
                r = self.sb.table("shops").select("*").eq("id", str(shop_id)).execute()
            else:
                r = self.sb.table("shops").update(patch).eq("id", str(shop_id)).execute()
            if not r.data:
                raise KeyError(f"shop {shop_id} not found")
            return _row_to_shop(r.data[0])

        return await asyncio.to_thread(_q)

    # --- shopkeepers ---
    async def get_shopkeeper_by_telegram_id(self, telegram_id: int) -> Shopkeeper | None:
        def _q() -> Shopkeeper | None:
            r = (
                self.sb.table("shopkeepers")
                .select("*")
                .eq("telegram_id", telegram_id)
                .maybe_single()
                .execute()
            )
            return _row_to_shopkeeper(r.data) if r and r.data else None

        return await asyncio.to_thread(_q)

    async def list_shopkeepers(self, shop_id: UUID) -> list[Shopkeeper]:
        def _q() -> list[Shopkeeper]:
            r = self.sb.table("shopkeepers").select("*").eq("shop_id", str(shop_id)).execute()
            return [_row_to_shopkeeper(row) for row in (r.data or [])]

        return await asyncio.to_thread(_q)

    async def create_shopkeeper(
        self, shop_id: UUID, telegram_id: int, name: str | None = None, is_owner: bool = False
    ) -> Shopkeeper:
        def _q() -> Shopkeeper:
            payload: dict[str, Any] = {
                "shop_id": str(shop_id),
                "telegram_id": telegram_id,
                "is_owner": is_owner,
            }
            if name:
                payload["name"] = name
            r = self.sb.table("shopkeepers").insert(payload).execute()
            return _row_to_shopkeeper(r.data[0])

        return await asyncio.to_thread(_q)

    # --- usage (ADR-006) ---
    async def upsert_usage(
        self, client_id: UUID, shop_id: UUID, day: date, metric: str, count: int
    ) -> None:
        def _q() -> None:
            payload = {
                "client_id": str(client_id),
                "shop_id": str(shop_id),
                "day": day.isoformat(),
                "metric": metric,
                "count": count,
            }
            # upsert on the unique (client_id, shop_id, day, metric)
            self.sb.table("usage_daily").upsert(payload, on_conflict="client_id,shop_id,day,metric").execute()

        await asyncio.to_thread(_q)

    async def get_usage(self, client_id: UUID, day: date) -> list[UsageDailyPoint]:
        def _q() -> list[UsageDailyPoint]:
            r = (
                self.sb.table("usage_daily")
                .select("*")
                .eq("client_id", str(client_id))
                .eq("day", day.isoformat())
                .execute()
            )
            return [
                UsageDailyPoint(
                    client_id=row["client_id"],
                    shop_id=row["shop_id"],
                    day=row["day"],
                    metric=row["metric"],
                    count=row["count"],
                )
                for row in (r.data or [])
            ]

        return await asyncio.to_thread(_q)

    async def health_check(self) -> bool:
        def _q() -> bool:
            self.sb.table("shops").select("id").limit(1).execute()  # cheap round-trip; raises if DB down
            return True

        return await asyncio.to_thread(_q)
