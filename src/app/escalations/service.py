"""Human escalation & handover (SPEC §3; ADR-009).

Lifecycle:
    escalate()  -> row in pending_escalations, shopkeeper notified, AI frozen for that customer
    is_frozen() -> pipeline routes the customer's next messages to the shopkeeper, not the AI
    reply()     -> shopkeeper answers the customer in the shop's voice
    handover()  -> AI unfrozen, escalation resolved. No "context restore" is needed: every turn
                   was recorded in Redis all along (see context.py), so the AI simply resumes.

Notification failures never propagate. A shopkeeper's Telegram being unreachable must not stop
the customer being told a specialist is coming, nor turn a degraded reply into no reply (ADR-009).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from app.escalations.context import remember
from app.telegram_bot.notify import send_to_customer, send_to_owner, send_to_shopkeepers
from app.tenants.models import Shop

logger = logging.getLogger(__name__)

_FROZEN_KEY = "escalation:frozen:{shop_id}:{identity}"

# ponytail: a freeze self-expires after 7 days. ceiling: a forgotten escalation silently
# hands the customer back to the AI. upgrade: an owner report of stale escalations (Stage 10).
FREEZE_TTL_SECONDS = 604_800


class NoPendingEscalation(Exception):
    """`/reply` or `/handover` for a customer this shop has not escalated."""


class DeliveryFailed(Exception):
    """The shopkeeper's message could not be delivered to the customer."""


def frozen_key(shop_id: UUID, identity: str) -> str:
    return _FROZEN_KEY.format(shop_id=shop_id, identity=identity)


# --- freeze state (Redis; SPEC §3 step 4) ---
async def is_frozen(redis: Any, shop_id: UUID, identity: str) -> bool:
    return bool(await redis.exists(frozen_key(shop_id, identity)))


async def freeze(redis: Any, shop_id: UUID, identity: str) -> None:
    await redis.set(frozen_key(shop_id, identity), "1", ex=FREEZE_TTL_SECONDS)


async def unfreeze(redis: Any, shop_id: UUID, identity: str) -> None:
    await redis.delete(frozen_key(shop_id, identity))


# --- pending_escalations table ---
def _sb(client: Any | None) -> Any:
    from app.db.supabase_client import get_supabase

    return client if client is not None else get_supabase()


async def _open_escalation(shop_id: UUID, identity: str, message: str, client: Any | None) -> None:
    sb = _sb(client)

    def _q() -> None:
        sb.table("pending_escalations").insert(
            {"shop_id": str(shop_id), "phone": identity, "message": message}
        ).execute()

    await asyncio.to_thread(_q)


async def _resolve_escalation(shop_id: UUID, identity: str, client: Any | None) -> int:
    """Mark this shop's open escalations for the customer resolved. Returns how many."""
    sb = _sb(client)

    def _q() -> int:
        r = (
            sb.table("pending_escalations")
            .update({"resolved_at": "now()"})
            .eq("shop_id", str(shop_id))  # tenant guard: never resolve another shop's row
            .eq("phone", identity)
            .is_("resolved_at", "null")
            .execute()
        )
        return len(r.data or [])

    return await asyncio.to_thread(_q)


async def count_open(shop_id: UUID | None = None, client: Any | None = None) -> int:
    """Open escalations (resolved_at null). All shops when shop_id is None (owner dashboard, §12)."""
    sb = _sb(client)

    def _q() -> int:
        q = sb.table("pending_escalations").select("id", count="exact").is_("resolved_at", "null")
        if shop_id is not None:
            q = q.eq("shop_id", str(shop_id))
        return q.execute().count or 0

    return await asyncio.to_thread(_q)


async def list_open(limit: int = 10, client: Any | None = None) -> list[dict]:
    """Most recent open escalations for `/owner escalations` (§12)."""
    sb = _sb(client)

    def _q() -> list[dict]:
        return (
            sb.table("pending_escalations").select("shop_id,phone,message,created_at")
            .is_("resolved_at", "null").order("created_at", desc=True).limit(limit).execute().data or []
        )

    return await asyncio.to_thread(_q)


async def _shopkeepers(shop_id: UUID) -> list:
    from app.db.factory import get_tenant_repo

    return await get_tenant_repo().list_shopkeepers(shop_id)


# --- the flow (SPEC §3) ---
async def escalate(
    redis: Any, shop: Shop, identity: str, message: str, reason: str, client: Any | None = None
) -> None:
    """SPEC §3 steps 1, 2, 4. The customer-facing line (step 3) is the caller's to return."""
    await _open_escalation(shop.id, identity, message, client)
    await freeze(redis, shop.id, identity)  # freeze BEFORE notifying: the next message must not hit the AI

    text = (
        f"⚠️ Escalation from {identity}: {message}\n"
        f"Reason: {reason}\n\n"
        f"Reply to the customer:  /reply {identity} <your message>\n"
        f"Give it back to the assistant:  /handover {identity}"
    )
    reached = await send_to_shopkeepers(shop, await _shopkeepers(shop.id), text)
    if reached == 0:
        # The customer has been promised a specialist and nobody heard. That is an owner problem.
        await send_to_owner(
            f"🚨 Escalation unread — no shopkeeper reachable\n"
            f"Shop: {shop.name}\nCustomer: {identity}\nMessage: {message}\n"
            f"Action: customer told a specialist is coming; AI frozen. Nobody was notified."
        )


async def alert_owner(shop: Shop, identity: str, problem: str, action: str) -> None:
    """Technical failure. Owner only — never the customer, never the shopkeeper (ADR-009)."""
    await send_to_owner(
        f"🚨 System problem\nShop: {shop.name}\nCustomer: {identity}\n"
        f"Problem: {problem}\nAction taken: {action}"
    )


async def forward_to_shopkeepers(shop: Shop, identity: str, text: str) -> None:
    """A frozen (or bypassed) customer's message goes to the humans, not the AI (SPEC §3 step 4, §8)."""
    await send_to_shopkeepers(
        shop, await _shopkeepers(shop.id), f"💬 {identity}: {text}\n\nReply:  /reply {identity} <text>"
    )


async def reply(redis: Any, shop: Shop, identity: str, text: str) -> None:
    """`/reply {phone} {text}` — shopkeeper answers the customer, in the shop's voice (SPEC §3)."""
    if not await is_frozen(redis, shop.id, identity):
        raise NoPendingEscalation(identity)
    if not await send_to_customer(shop, identity, text):
        raise DeliveryFailed(identity)
    # Recorded as a real turn: after /handover the AI must know what the human already said.
    await remember(redis, shop.id, identity, "shopkeeper", text)


async def handover(
    redis: Any, shop: Shop, identity: str, client: Any | None = None
) -> None:
    """`/handover {phone}` — the AI takes the conversation back (SPEC §3).

    No context restore: `context.remember()` recorded every turn, including the shopkeeper's,
    so the AI resumes mid-conversation with full history.
    """
    if not await is_frozen(redis, shop.id, identity):
        raise NoPendingEscalation(identity)
    await resolve_escalation(redis, shop.id, identity, client)


async def resolve_escalation(
    redis: Any, shop_id: UUID, identity: str, client: Any | None = None
) -> int:
    """Close the open escalation(s) for a customer and let the AI answer them again.

    `/reply` alone leaves the row open forever — the owner's ✔️ Resolve button and `/handover`
    both land here. Returns how many rows were closed (0 = nothing was open).
    """
    closed = await _resolve_escalation(shop_id, identity, client)
    await unfreeze(redis, shop_id, identity)
    return closed
