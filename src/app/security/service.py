"""Security state: quarantine, blacklist, bypass, incident capture (SPEC §7, §8; ADR-009).

Owns the Redis keys the pipeline reads on the hot path and the durable records the owner
investigates. The pipeline calls `is_quarantined` / `is_bypassed` / `is_blacklisted` here
rather than inlining the key strings, so writer and reader can never drift.

Hot-path checks are Redis-only (SPEC §11 "all state in Redis"). Postgres holds the audit
trail: `security_incidents` (forensic snapshot) and `blacklisted_phones` (durable record).

ponytail: blacklist hot-path truth lives in Redis (`blacklist:{identity}`, no TTL); the DB row
is the durable/audit copy. ceiling: a Redis flush drops the hot-path block until re-set. upgrade:
rehydrate Redis from `blacklisted_phones` on startup (Stage 10/12) if a flush ever bites.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from app.escalations.context import history
from app.security.detectors import AttackResult
from app.telegram_bot.notify import send_to_owner
from app.tenants.models import Shop

logger = logging.getLogger(__name__)

# --- Redis key templates (shared with the pipeline via the functions below) ---
_QUARANTINE_KEY = "quarantine:{identity}"
_BYPASS_KEY = "bypass_ai:{identity}"
_BLACKLIST_KEY = "blacklist:{identity}"
_RATE_KEY = "rate:{identity}"
_DAYRATE_KEY = "dayrate:{identity}"

QUARANTINE_TTL_SECONDS = 3_600  # SPEC §7: 1-hour quarantine
QUARANTINE_EXTEND_SECONDS = 86_400  # /quarantine_extend → 24h. ponytail: fixed; make an arg if asked.
RATE_WINDOW_SECONDS = 60  # SPEC §7 rapid-fire window
DAY_WINDOW_SECONDS = 86_400  # per-customer daily cap window


def quarantine_key(identity: str) -> str:
    return _QUARANTINE_KEY.format(identity=identity)


def bypass_key(identity: str) -> str:
    return _BYPASS_KEY.format(identity=identity)


def blacklist_key(identity: str) -> str:
    return _BLACKLIST_KEY.format(identity=identity)


# --- hot-path reads (called by messaging/pipeline.py) ---
async def is_quarantined(redis: Any, identity: str) -> bool:
    return bool(await redis.exists(quarantine_key(identity)))


async def is_bypassed(redis: Any, identity: str) -> bool:
    return bool(await redis.exists(bypass_key(identity)))


async def is_blacklisted(redis: Any, identity: str) -> bool:
    return bool(await redis.exists(blacklist_key(identity)))


async def bump_rate(redis: Any, identity: str) -> int:
    """Increment this customer's 60-second message counter and return the new count."""
    key = _RATE_KEY.format(identity=identity)
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, RATE_WINDOW_SECONDS)
    return count


async def bump_daily(redis: Any, identity: str) -> int:
    """Increment this customer's 24h AI-message counter and return the new count (cost/abuse cap)."""
    key = _DAYRATE_KEY.format(identity=identity)
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, DAY_WINDOW_SECONDS)
    return count


# --- Supabase helper (mirrors escalations/service.py) ---
def _sb(client: Any | None) -> Any:
    from app.db.supabase_client import get_supabase

    return client if client is not None else get_supabase()


# --- quarantine + incident capture (SPEC §7) ---
async def quarantine(
    redis: Any, shop: Shop, identity: str, attack: AttackResult, client: Any | None = None
) -> str | None:
    """Auto-quarantine an attacker: Redis lock (1h), snapshot the last 25 msgs, alert the owner.

    Returns the incident id (None if the DB write failed — quarantine + alert still happen).
    """
    await redis.set(quarantine_key(identity), attack.attack_type, ex=QUARANTINE_TTL_SECONDS)

    snapshot = await history(redis, shop.id, identity)  # last 25, oldest→newest (SPEC §7)
    incident_id = await _write_incident(shop.id, identity, attack, snapshot, client)

    await send_to_owner(
        f"🛡 Attack detected — customer auto-quarantined (1h)\n"
        f"Shop: {shop.name}\nCustomer: {identity}\n"
        f"Type: {attack.attack_type}  ·  Trigger: {attack.matched!r}\n"
        f"Incident: {incident_id or 'DB write failed'}\n"
        f"Captured: {len(snapshot)} recent message(s)\n\n"
        f"Investigate:  /investigate {incident_id}\n"
        f"Lift:  /quarantine_lift {identity}   ·   Extend:  /quarantine_extend {identity}\n"
        f"Blacklist:  /blacklist {identity}   ·   Send to shop:  /forward_to_shop {identity} <shop_id>"
    )
    return incident_id


async def _write_incident(
    shop_id: UUID, identity: str, attack: AttackResult, snapshot: list[dict], client: Any | None
) -> str | None:
    sb = _sb(client)

    def _q() -> str | None:
        r = (
            sb.table("security_incidents")
            .insert(
                {
                    "shop_id": str(shop_id),
                    "phone": identity,
                    "attack_type": attack.attack_type,
                    "message_snapshot": snapshot,
                }
            )
            .execute()
        )
        rows = r.data or []
        return rows[0]["id"] if rows else None

    try:
        return await asyncio.to_thread(_q)
    except Exception:
        logger.exception("security_incident write failed shop=%s identity=%s", shop_id, identity)
        return None


# --- owner investigation ops (SPEC §7) ---
async def recent_incidents(limit: int = 10, client: Any | None = None) -> list[dict]:
    """Most recent security incidents for `/owner security` (§12)."""
    sb = _sb(client)

    def _q() -> list[dict]:
        return (
            sb.table("security_incidents").select("id,shop_id,phone,attack_type,status,created_at")
            .order("created_at", desc=True).limit(limit).execute().data or []
        )

    return await asyncio.to_thread(_q)


async def get_incident(incident_id: str, client: Any | None = None) -> dict | None:
    sb = _sb(client)

    def _q() -> dict | None:
        r = sb.table("security_incidents").select("*").eq("id", incident_id).limit(1).execute()
        rows = r.data or []
        return rows[0] if rows else None

    return await asyncio.to_thread(_q)


async def lift_quarantine(redis: Any, identity: str) -> None:
    await redis.delete(quarantine_key(identity))


async def extend_quarantine(redis: Any, identity: str) -> None:
    """Re-arm the quarantine for a longer window (owner decided this one is a real threat)."""
    await redis.set(quarantine_key(identity), "extended", ex=QUARANTINE_EXTEND_SECONDS)


async def blacklist(redis: Any, identity: str, shop_id: UUID | None, reason: str, client: Any | None = None) -> None:
    """Permanently block a number: Redis hot-path key + durable DB row. Also lifts any quarantine."""
    await redis.set(blacklist_key(identity), reason or "blacklisted")  # no TTL — permanent
    await redis.delete(quarantine_key(identity))
    sb = _sb(client)

    def _q() -> None:
        sb.table("blacklisted_phones").upsert(
            {"phone": identity, "shop_id": str(shop_id) if shop_id else None, "reason": reason}
        ).execute()

    try:
        await asyncio.to_thread(_q)
    except Exception:
        logger.exception("blacklist DB write failed identity=%s", identity)  # Redis block still holds


# --- direct-to-shop bypass (SPEC §8) ---
async def set_bypass(redis: Any, identity: str) -> None:
    """Route this number straight to the shop's staff, no AI. Persistent until removed."""
    await redis.set(bypass_key(identity), "1")  # no TTL


async def remove_bypass(redis: Any, identity: str) -> None:
    await redis.delete(bypass_key(identity))


async def forward_to_shop(redis: Any, identity: str) -> None:
    """Owner clears a (false-positive) quarantine and routes the number to the shop instead."""
    await redis.delete(quarantine_key(identity))
    await set_bypass(redis, identity)
