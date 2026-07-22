"""Celery tasks — thin wrappers over module logic (SPEC §11).

`process_whatsapp_message` is the Twilio-path entry (ADR-002). It stays dormant
until Stage 13: Telegram-first testing drives the pipeline inline via the
customer bot. Kept as a working skeleton so Stage 13 is a wiring flip, not a build.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="process_whatsapp_message")
def process_whatsapp_message(
    shop_id: str, identity: str, body: str, message_sid: str | None = None
) -> str:
    """Run the SPEC §9 pipeline for one inbound WhatsApp message. Returns the pipeline action."""
    from app.db.factory import get_tenant_repo
    from app.db.redis_client import get_redis

    return asyncio.run(_run(get_tenant_repo(), get_redis(), shop_id, identity, body, message_sid))


async def _run(
    repo: Any, redis: Any, shop_id: str, identity: str, body: str, message_sid: str | None
) -> str:
    """Testable core: fetch shop, run pipeline. Deps injected so tests skip Celery + real IO."""
    from app.messaging.pipeline import InboundMessage, process_message

    shop = await repo.get_shop_by_id(UUID(shop_id))
    if shop is None:
        logger.warning("process_whatsapp_message: unknown shop_id=%s", shop_id)
        return "unknown_shop"
    result = await process_message(InboundMessage(shop, identity, body, message_sid), redis)
    # ponytail: reply computed but not sent back over WhatsApp. ceiling: no Twilio outbound.
    # upgrade: Stage 13 sends result.reply via the whatsapp/ outbound client.
    logger.info("pipeline shop=%s identity=%s action=%s", shop_id, identity, result.action)
    return result.action


# ---------------------------------------------------------------------------
# Daily usage flush (ADR-006): drain Redis usage counters → usage_daily (billing).
# The hot path only touches Redis (no per-message DB write); this is what makes
# that data durable. Without it, counters expire on their 2-day TTL — a billing leak.
# ---------------------------------------------------------------------------
@celery_app.task(name="flush_usage_counters")
def flush_usage_counters() -> int:
    """Beat entry: flush completed-day usage counters into usage_daily. Returns keys flushed."""
    return asyncio.run(_flush_task())


async def _flush_task() -> int:
    # Fresh Redis per run: the task runs in its own asyncio.run loop, and a cached async client
    # would break ('Event loop is closed') on the second beat tick.
    from app.db.factory import get_tenant_repo
    from app.db.redis_client import new_redis

    redis = new_redis()
    try:
        return await flush_usage(get_tenant_repo(), redis)
    finally:
        await redis.aclose()


async def flush_usage(repo: Any, redis: Any, *, today: date | None = None) -> int:
    """Testable core. Drain every `usage:*` key into `usage_daily`.

    A **completed** day is final: read-once + delete (`getdel`), so a re-run is a no-op.

    **Today** is still incrementing, so it is read WITHOUT deleting and upserted as a running
    total. `upsert_usage` overwrites the row's count, so every pass simply replaces today's number
    with the newer one and the final pass after midnight writes the true total — nothing is lost
    or double-counted. Without this the console shows nothing for the current day, which is the
    day an operator actually cares about.
    """
    today = today or datetime.now(timezone.utc).date()
    from app.messaging.pipeline import USAGE_KEY_PREFIX, parse_usage_key

    flushed = 0
    async for key in redis.scan_iter(match=USAGE_KEY_PREFIX + "*"):
        parsed = parse_usage_key(key)
        if parsed is None:
            logger.warning("flush_usage: skipping malformed key %r", key)
            continue
        client_id, shop_id, day, metric = parsed
        count = await redis.get(key) if day >= today else await redis.getdel(key)
        if count is None:  # expired between scan and read
            continue
        await repo.upsert_usage(client_id, shop_id, day, metric, int(count))
        flushed += 1

    logger.info("flush_usage: flushed %d completed-day counter(s)", flushed)
    return flushed


# ---------------------------------------------------------------------------
# 60s health check (SPEC §13): probe subsystems, page the owner on failure.
# ---------------------------------------------------------------------------
@celery_app.task(name="health_check")
def health_check() -> str:
    """Beat entry: run the health check; alert the owner if unhealthy. Returns 'ok' | 'alerted'."""
    return asyncio.run(_health_task())


async def _health_task() -> str:
    from app.db.factory import get_tenant_repo
    from app.db.redis_client import new_redis

    redis = new_redis()  # fresh per run (see _flush_task)
    try:
        # include_celery=False: this task IS running on a worker, so a worker is alive by
        # construction; and control.ping() can't be answered by a busy solo-pool worker (Windows).
        # Worker liveness belongs to the external /health poll, not to a worker pinging itself.
        return await run_health_check(get_tenant_repo(), redis, include_celery=False)
    finally:
        await redis.aclose()


async def run_health_check(repo: Any, redis: Any, *, include_celery: bool = True) -> str:
    """Testable core. Same checker the `/health` endpoint uses; a failure pages the owner.

    Uses `send_to_owner` (not `escalations.alert_owner`, whose signature is shop+customer-scoped) —
    a system-wide health failure has no shop/customer. ADR-009: only the owner learns of a failure.
    """
    from app.console.service import drain_ops, publish_snapshot
    from app.reports.health import check_health, format_health
    from app.telegram_bot.notify import send_to_owner

    report = await check_health(redis, repo, include_celery=include_celery)

    # Platform-owner console channel (migrations 024/025): publish health where the console can
    # read it, and execute anything it queued (quarantine/bypass/blacklist/llm-test). Both are
    # best-effort by construction — neither can change the health verdict or raise.
    await publish_snapshot(report, redis)
    await drain_ops(redis)

    if not report.ok:
        await send_to_owner("🚨 CRITICAL — health check failed\n\n" + format_health(report))
        return "alerted"
    return "ok"


# ---------------------------------------------------------------------------
# In-process heartbeat: the same two beat jobs, run from the bot process.
# ---------------------------------------------------------------------------
HEARTBEAT_SECONDS = 60
FLUSH_EVERY_TICKS = 10  # usage lands in the console ~10 min after it happens

async def heartbeat_forever(
    *, interval: float = HEARTBEAT_SECONDS, flush_every: int = FLUSH_EVERY_TICKS
) -> None:
    """Run the health snapshot (and periodically the usage flush) forever, in-process.

    Celery Beat owns these in a Docker deployment, but the owner runs the bots alone on a PC —
    without this the console's health snapshot is never published (strip reads "backend offline"
    while the bots are up) and queued console operations are never drained. Both jobs are
    idempotent, so a Beat running alongside is harmless. Cancelled by the caller on shutdown.
    """
    tick = 0
    while True:
        try:
            await _health_task()
            if tick % flush_every == 0:
                await _flush_task()
        except asyncio.CancelledError:
            raise
        except Exception:  # a heartbeat must never take the bots down with it
            logger.exception("heartbeat tick failed")
        tick += 1
        await asyncio.sleep(interval)
