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
#
# SCOPE, decided rather than inherited. Sessions have always been per shop
# (`session:{shop_id}:{identity}`); these were not, and that only stayed invisible because a
# Telegram customer would have to go and message several shops. On WhatsApp `identity` is a stable
# phone number, so one person tripping the attack detector at one shop would have been answered
# with "Your message could not be processed." by all 30, and one person's daily AI cap would have
# been shared across every shop on the platform. The shops are independent businesses.
#
#   per shop  — quarantine, rate, dayrate: set automatically by the detector or the meter, and
#               about one conversation with one shop.
#   platform  — blacklist, bypass_ai: both are platform-OWNER commands (see the owner security
#               commands in telegram_bot/bot.py). Banning someone from one shop but not the other
#               29 is not what the owner means when they type /blacklist.
_QUARANTINE_KEY = "quarantine:{shop_id}:{identity}"
_BYPASS_KEY = "bypass_ai:{identity}"
_BLACKLIST_KEY = "blacklist:{identity}"
_RATE_KEY = "rate:{shop_id}:{identity}"
_DAYRATE_KEY = "dayrate:{shop_id}:{identity}"

QUARANTINE_TTL_SECONDS = 3_600  # SPEC §7: 1-hour quarantine
QUARANTINE_EXTEND_SECONDS = 86_400  # /quarantine_extend → 24h. ponytail: fixed; make an arg if asked.
RATE_WINDOW_SECONDS = 60  # SPEC §7 rapid-fire window
DAY_WINDOW_SECONDS = 86_400  # per-customer daily cap window


def quarantine_key(shop_id: Any, identity: str) -> str:
    return _QUARANTINE_KEY.format(shop_id=shop_id, identity=identity)


def quarantine_glob(identity: str = "*", shop_id: Any = "*") -> str:
    """Match pattern for quarantine keys — every shop by default."""
    return _QUARANTINE_KEY.format(shop_id=shop_id, identity=identity)


def identity_from_quarantine_key(key: str) -> str:
    """`quarantine:{shop_id}:{identity}` → identity. Shop ids contain no ':', so split from the
    left twice; the identity itself is returned whole even if it somehow contains one."""
    parts = key.split(":", 2)
    return parts[2] if len(parts) == 3 else key


async def clear_quarantine_everywhere(redis: Any, identity: str) -> int:
    """Lift this person's quarantine at EVERY shop. Returns how many were cleared.

    The owner's commands take an identity, not a shop — "/quarantine_lift +971..." means "this
    person is not a threat", not "this person is not a threat at one of my thirty shops". The keys
    are per shop so one shop's detector cannot silence a customer at the other twenty-nine; lifting
    is the deliberate opposite.
    """
    cleared = 0
    async for key in redis.scan_iter(match=quarantine_glob(identity)):
        await redis.delete(key)
        cleared += 1
    return cleared


def bypass_key(identity: str) -> str:
    return _BYPASS_KEY.format(identity=identity)


def blacklist_key(identity: str) -> str:
    return _BLACKLIST_KEY.format(identity=identity)


# --- hot-path reads (called by messaging/pipeline.py) ---
async def is_quarantined(redis: Any, shop_id: Any, identity: str) -> bool:
    return bool(await redis.exists(quarantine_key(shop_id, identity)))


async def is_bypassed(redis: Any, identity: str) -> bool:
    return bool(await redis.exists(bypass_key(identity)))


async def is_blacklisted(redis: Any, identity: str) -> bool:
    return bool(await redis.exists(blacklist_key(identity)))


async def bump_rate(redis: Any, shop_id: Any, identity: str) -> int:
    """Increment this customer's 60-second counter AT THIS SHOP and return the new count."""
    key = _RATE_KEY.format(shop_id=shop_id, identity=identity)
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, RATE_WINDOW_SECONDS)
    return count


async def bump_daily(redis: Any, shop_id: Any, identity: str) -> int:
    """Increment this customer's 24h AI-message counter AT THIS SHOP (cost/abuse cap)."""
    key = _DAYRATE_KEY.format(shop_id=shop_id, identity=identity)
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
    redis: Any, shop: Shop, identity: str, attack: AttackResult, client: Any | None = None,
    *, message: str = "",
) -> str | None:
    """Auto-quarantine an attacker: Redis lock (1h), snapshot the last 25 msgs, alert the owner.

    `message` is the triggering text itself — it is NOT in history yet (the pipeline stores
    messages only on the AI path), so without it a first-message attack captures nothing.

    Returns the incident id (None if the DB write failed — quarantine + alert still happen).
    """
    await redis.set(
        quarantine_key(shop.id, identity), attack.attack_type, ex=QUARANTINE_TTL_SECONDS
    )

    snapshot = await history(redis, shop.id, identity)  # last 25, oldest→newest (SPEC §7)
    if message:
        snapshot = [*snapshot, {"role": "customer", "content": message}]
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
    await clear_quarantine_everywhere(redis, identity)


async def extend_quarantine(redis: Any, identity: str) -> None:
    """Re-arm the quarantine for a longer window (owner decided this one is a real threat).

    Only where a quarantine already exists: someone can be a threat at the shop they attacked
    without being pre-emptively blocked at twenty-nine they have never messaged. To block
    everywhere, the owner has /blacklist."""
    async for key in redis.scan_iter(match=quarantine_glob(identity)):
        await redis.set(key, "extended", ex=QUARANTINE_EXTEND_SECONDS)


async def blacklist(redis: Any, identity: str, shop_id: UUID | None, reason: str, client: Any | None = None) -> None:
    """Permanently block a number: Redis hot-path key + durable DB row. Also lifts any quarantine."""
    await redis.set(blacklist_key(identity), reason or "blacklisted")  # no TTL — permanent
    await clear_quarantine_everywhere(redis, identity)
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
    await clear_quarantine_everywhere(redis, identity)
    await set_bypass(redis, identity)
