"""Message processing pipeline (SPEC §9) — channel-agnostic.

Runs the exact ordered steps SPEC §9 defines, after a channel adapter (the
Twilio webhook in prod, the customer Telegram bot in Telegram-first testing —
ADR-002) has normalized the inbound message into an `InboundMessage`.

Steps whose backing feature lands in a later stage carry a `ponytail:` marker
naming that stage. Live now: step 2 (suspension) and step 7 (usage meter + the
Stage 4 AI service). Steps 4/5 do real Redis reads — only their *setters* are Stage 7/8.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from app.ai.orchestrator import answer_customer
from app.escalations.context import remember
from app.escalations.service import forward_to_shopkeepers, still_frozen
from app.security.detectors import MAX_MESSAGE_CHARS, detect_attack
from app.core.config import settings
from app.security.service import bump_daily, bump_rate, is_blacklisted, is_bypassed, is_quarantined, quarantine
from app.tenants.models import Shop, ShopStatus

logger = logging.getLogger(__name__)

# Redis key templates. Quarantine/bypass/blacklist keys live in security/service.py so the
# writer (owner commands, attack detector) and reader (this pipeline) can never drift (ADR-009).
USAGE_KEY_PREFIX = "usage:"  # writer owns the format; the beat flush (tasks.py) parses it back
_USAGE_KEY = USAGE_KEY_PREFIX + "{client_id}:{shop_id}:{day}:{metric}"
_USAGE_TTL_SECONDS = 172_800  # 2 days — safety net if the Stage 10 flush job misses a day


def parse_usage_key(key: str) -> tuple[UUID, UUID, date, str] | None:
    """Inverse of `_USAGE_KEY`. Returns (client_id, shop_id, day, metric) or None if malformed.

    UUIDs and an ISO date contain no ':', so a plain 5-way split is unambiguous.
    """
    parts = key.split(":")
    if len(parts) != 5 or parts[0] != "usage":
        return None
    try:
        return UUID(parts[1]), UUID(parts[2]), date.fromisoformat(parts[3]), parts[4]
    except ValueError:
        return None

_QUARANTINE_REPLY = "Your message could not be processed."
_TOO_LONG_REPLY = (
    "That's a lot to take in! Could you shorten it to a sentence or two so I can help you properly?"
)

# §11 concurrency/reliability. All state in Redis (no local memory).
_LOCK_KEY = "lock:session:{shop_id}:{identity}"
_LOCK_TTL = 30  # seconds — a session's messages are serialized (SPEC §11)
_DEDUP_KEY = "dedup:{sid}"
_DEDUP_TTL = 300  # 5 min — a redelivered Twilio MessageSid is dropped (SPEC §11)


def lock_key(shop_id: Any, identity: str) -> str:
    return _LOCK_KEY.format(shop_id=shop_id, identity=identity)


async def _is_duplicate(redis: Any, sid: str) -> bool:
    """True if this MessageSid was already seen (SET NX EX). First delivery sets it and returns False."""
    first = await redis.set(_DEDUP_KEY.format(sid=sid), "1", nx=True, ex=_DEDUP_TTL)
    return not first


@dataclass(frozen=True)
class InboundMessage:
    """A normalized inbound customer message, channel-agnostic (SPEC §1, §9).

    `identity` is the customer key: phone number on WhatsApp/prod, Telegram user
    id during testing (ADR-002/005). Both flow into the same field so the Stage 13
    WhatsApp cutover swaps only the adapter, never the pipeline.
    """

    shop: Shop
    identity: str
    text: str
    message_sid: str | None = None  # Twilio dedup id; None on the Telegram path


@dataclass(frozen=True)
class PipelineResult:
    """Pipeline outcome. `reply` None = stay silent (SPEC §9 steps 3/5)."""

    reply: str | None
    action: str  # which step decided — for logging + tests
    media: tuple = ()  # product photos/video the AI chose to show; the channel adapter sends them


async def process_message(msg: InboundMessage, redis: Any) -> PipelineResult:
    """Run the SPEC §9 pipeline for one inbound message (step 1 sig-verify is upstream).

    §11 wraps the pipeline in a per-session lock (serialize a customer's overlapping messages) and
    MessageSid dedup (drop a redelivered Twilio message). **Lock first, dedup second:** a lock miss
    returns before the SID is marked seen, so the Celery retry that the Twilio path will do at
    Stage 13 re-runs cleanly instead of being deduped away. The live Telegram path is sequential
    per bot (no `concurrent_updates`), so `locked` never fires there today.
    """
    lock = lock_key(msg.shop.id, msg.identity)
    if not await redis.set(lock, "1", nx=True, ex=_LOCK_TTL):
        # Another message for this session is mid-flight. Telegram: unreachable (sequential).
        # ponytail: Twilio/Celery path should `self.retry` on this action once it's live (Stage 13).
        return await _logged(msg, PipelineResult(None, "locked"))
    try:
        if msg.message_sid and await _is_duplicate(redis, msg.message_sid):
            return await _logged(msg, PipelineResult(None, "duplicate"))
        return await _logged(msg, await _dispatch(msg, redis))
    finally:
        # ponytail: plain DEL release (no owner token). ceiling: if processing outlives the 30s TTL
        # the lock can be re-acquired by another message and this DEL frees that one too.
        # upgrade: compare-and-delete with a per-hold token (Lua) if a session can run >30s.
        await redis.delete(lock)


async def _logged(msg: InboundMessage, result: PipelineResult) -> PipelineResult:
    """Record every NON-AI outcome in `pipeline_events` (migration 025) so the platform owner can
    answer "why did this customer never get a reply?". `ai` is the normal path and would be a row
    per message — the archive already has those. Fire-and-forget: this must never delay or break
    a reply, so failures are swallowed."""
    if result.action == "ai":
        return result
    try:
        from app.db.supabase_client import get_supabase

        sb = get_supabase()
        await asyncio.to_thread(
            lambda: sb.table("pipeline_events").insert(
                {"shop_id": str(msg.shop.id), "identity": msg.identity, "action": result.action}
            ).execute()
        )
    except Exception:  # noqa: BLE001 — observability must never cost a customer their answer
        logger.debug("pipeline_events insert failed (action=%s)", result.action, exc_info=True)
    return result


async def _dispatch(msg: InboundMessage, redis: Any) -> PipelineResult:
    """Steps 2–7 of the SPEC §9 pipeline, run under the session lock."""
    shop = msg.shop

    # Step 2 — shop suspended → auto-reply, stop (SPEC §2, §9). LIVE.
    if shop.status == ShopStatus.SUSPENDED:
        return PipelineResult(_suspended_reply(shop), "suspended")

    # Step 3 — blacklist → silent ignore (SPEC §9). Redis hot-path check (security/service.py).
    if await is_blacklisted(redis, msg.identity):
        return PipelineResult(None, "blacklisted")

    # Step 4 — quarantine → generic reply (SPEC §7). Set by step 6 or the owner's /blacklist path.
    if await is_quarantined(redis, msg.identity):
        return PipelineResult(_QUARANTINE_REPLY, "quarantined")

    # Step 4b — AI frozen by an escalation (SPEC §3 step 4). The shopkeeper owns this
    # conversation now; the customer keeps talking to the same channel and never sees a change.
    # DB-verified: the dashboard resolves escalations without Redis, so a Redis "frozen" is
    # confirmed against pending_escalations and lazily unfrozen when the row was closed there.
    if await still_frozen(redis, shop.id, msg.identity):
        await _to_humans(redis, shop, msg, "escalation_frozen")
        return PipelineResult(None, "frozen")

    # Step 5 — bypass_ai → forward to shopkeeper, no AI (SPEC §8). Same action as a freeze.
    if await is_bypassed(redis, msg.identity):
        await _to_humans(redis, shop, msg, "bypass")
        return PipelineResult(None, "bypass")

    # Step 6 — attack detection → auto-quarantine + owner alert (SPEC §7). Bump the rapid-fire
    # counter first so a flood trips even when each individual message looks innocent.
    attack = detect_attack(msg.text, msg_count_60s=await bump_rate(redis, msg.identity))
    if attack is not None:
        await quarantine(redis, shop, msg.identity, attack, message=msg.text)
        return PipelineResult(_QUARANTINE_REPLY, "attack")

    # Over-length but clean (detect_attack already ruled out injection payloads above): a verbose
    # customer is not an attacker. Ask them to shorten instead of quarantining — and never send an
    # oversized prompt to the LLM (bounds token cost at 30 shops × high volume).
    if len(msg.text) > MAX_MESSAGE_CHARS:
        return PipelineResult(_TOO_LONG_REPLY, "too_long")

    # Cost/abuse ceiling: a per-customer DAILY cap on AI-answered messages. Rapid-fire (step 6)
    # stops 60-second bursts; this stops a sustained flood just under that threshold from running up
    # the LLM bill (matters at 30 shops × high volume). Far above any real customer; 0 disables.
    if settings.ai_daily_msg_cap and await bump_daily(redis, msg.identity) > settings.ai_daily_msg_cap:
        logger.warning("daily AI cap hit identity=%s shop=%s", msg.identity, shop.id)
        return PipelineResult(_QUARANTINE_REPLY, "rate_capped")

    # Step 7 — normal AI processing (SPEC §9). Usage meter (ADR-006) + AI service (Stage 4).
    await _incr_usage(redis, shop, "messages")
    media: list = []  # the AI may choose to show product photos/video (SPEC §4 step 10)
    usage: dict = {}  # llm_calls + token counts, for per-shop billing (console Analytics)
    reply = await answer_customer(
        shop, msg.identity, msg.text, redis, media_sink=media, usage_sink=usage
    )
    # Metered after the answer so a slow/failed LLM never delays the reply. Tokens are what the
    # platform owner is actually billed for; ai_calls is one per LLM round-trip, not per message.
    for metric in ("llm_calls", "tokens_in", "tokens_out"):
        if usage.get(metric):
            await _incr_usage(redis, shop, "ai_calls" if metric == "llm_calls" else metric,
                              by=usage[metric])
    return PipelineResult(reply, "ai", media=tuple(media))


async def _to_humans(redis: Any, shop: Shop, msg: InboundMessage, why: str) -> None:
    """Route a message to the shop's staff instead of the AI, and remember it was said.

    Recording the turn matters: after `/handover` the AI resumes this same conversation and must
    see what the customer said while a human held it. Delivery failures must not raise — the
    customer's message has already been accepted.
    """
    logger.info("to humans shop=%s identity=%s why=%s", shop.id, msg.identity, why)
    try:
        await remember(redis, shop.id, msg.identity, "customer", msg.text)
        await forward_to_shopkeepers(shop, msg.identity, msg.text)
    except Exception:
        logger.exception("forward to shopkeepers failed shop=%s identity=%s", shop.id, msg.identity)


# --- helpers ---
def _suspended_reply(shop: Shop) -> str:
    """Customer-safe suspension notice. Never leak the internal reason (SPEC §2)."""
    return f"{shop.name} is temporarily unavailable. Please try again later."


async def _incr_usage(redis: Any, shop: Shop, metric: str, *, by: int = 1) -> None:
    """Per-client daily usage counter (ADR-006). Flushed to usage_daily by the Stage 10 beat job."""
    day = datetime.now(timezone.utc).date().isoformat()
    key = _USAGE_KEY.format(client_id=shop.client_id, shop_id=shop.id, day=day, metric=metric)
    count = await redis.incrby(key, by)
    if count == by:  # first hit today → cap lifetime so a missed flush can't leak keys forever
        await redis.expire(key, _USAGE_TTL_SECONDS)
