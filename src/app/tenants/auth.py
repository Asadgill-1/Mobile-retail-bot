"""Tenant auth — owner + shopkeeper verification (SPEC §1).

Owner is identified by OWNER_TELEGRAM_ID (single global owner, SPEC §SYSTEM OVERVIEW).
Shopkeepers are identified by telegram_id → shop_id via the TenantRepo.
"""

from __future__ import annotations

from app.core.config import settings
from app.db.base import TenantRepo
from app.tenants.models import Shopkeeper


class NotAuthorized(Exception):
    pass


def is_owner(telegram_id: int) -> bool:
    """True if this Telegram user is the owner (SPEC §SYSTEM OVERVIEW, §1)."""
    return telegram_id == settings.owner_telegram_id


async def require_owner(telegram_id: int) -> None:
    """Raise NotAuthorized unless this is the owner. Use for owner-only commands."""
    if not is_owner(telegram_id):
        raise NotAuthorized(f"telegram_id {telegram_id} is not the owner")


async def resolve_shopkeeper(telegram_id: int, repo: TenantRepo) -> Shopkeeper:
    """Resolve a Telegram user to their shopkeeper row (SPEC §1). Raises if unknown."""
    sk = await repo.get_shopkeeper_by_telegram_id(telegram_id)
    if sk is None:
        raise NotAuthorized(f"telegram_id {telegram_id} is not a registered shopkeeper")
    return sk
