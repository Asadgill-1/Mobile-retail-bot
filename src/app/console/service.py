"""Backend half of the platform-owner console channel (migrations 024/025).

Two jobs, both driven from the existing 60s health beat so no new process/port exists:

  publish_snapshot()  — push live health + the quarantined list into `platform_settings`
                        so the console can render them without touching Redis. The console
                        treats a stale snapshot as "backend offline" — that IS the down signal.
  drain_ops()         — execute queued `redis_ops` rows through the REAL security functions
                        (same code the owner bot calls), stamping applied_at / error per row.

Everything here is best-effort: a console feature must never be able to break the health check
or the message pipeline. Each op is isolated — one bad row cannot stop the rest of the queue.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

HEALTH_KEY = "health_snapshot"
_MAX_OPS_PER_TICK = 25  # bounded work per beat; the queue drains over subsequent ticks


def _sb(client: Any | None) -> Any:
    from app.db.supabase_client import get_supabase

    return client if client is not None else get_supabase()


# --- platform_settings ------------------------------------------------------
async def set_setting(key: str, value: Any, client: Any | None = None) -> None:
    sb = _sb(client)

    def _q() -> None:
        sb.table("platform_settings").upsert(
            {"key": key, "value": value, "updated_at": "now()"}
        ).execute()

    await asyncio.to_thread(_q)


async def get_setting(key: str, client: Any | None = None) -> Any | None:
    sb = _sb(client)

    def _q() -> Any | None:
        rows = (
            sb.table("platform_settings").select("value").eq("key", key).limit(1).execute().data
            or []
        )
        return rows[0]["value"] if rows else None

    return await asyncio.to_thread(_q)


# --- health snapshot --------------------------------------------------------
async def _quarantined(redis: Any, limit: int = 200) -> list[str]:
    """Identities currently quarantined (the console's Security tab lists them)."""
    from app.security.service import quarantine_key

    prefix = quarantine_key("")
    out: list[str] = []
    async for key in redis.scan_iter(match=quarantine_key("*")):
        k = key.decode() if isinstance(key, bytes) else key
        out.append(k[len(prefix):] if k.startswith(prefix) else k)
        if len(out) >= limit:
            break
    return out


async def publish_snapshot(report: Any, redis: Any, client: Any | None = None) -> None:
    """Write the live health report + quarantined list where the console can read it."""
    try:
        try:
            quarantined = await _quarantined(redis)
        except Exception:  # noqa: BLE001 — a Redis scan hiccup must not drop the whole snapshot
            quarantined = []
        await set_setting(
            HEALTH_KEY,
            {
                "ok": bool(report.ok),
                "checks": dict(report.checks),
                "metrics": dict(report.metrics),
                "quarantined": quarantined,
                "at": datetime.now(timezone.utc).isoformat(),
            },
            client,
        )
    except Exception:  # noqa: BLE001
        logger.warning("console: health snapshot publish failed", exc_info=True)


# --- redis_ops outbox -------------------------------------------------------
async def _llm_test() -> dict:
    """One cheap completion with the CURRENT (console-set) config. Proves a model switch works."""
    from app.llm.llm_client import LLMMessage, get_llm_client

    llm = get_llm_client()
    llm._overlay_at = 0.0  # force a re-read: the console just saved new settings
    started = time.monotonic()
    resp = await llm.chat([LLMMessage(role="user", content="Reply with the single word: ready")])
    return {
        "ok": True,
        "model": llm.model,
        "provider": llm.provider,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "sample": (resp.content or "")[:200],
    }


async def _run_op(op: str, args: dict, redis: Any, client: Any | None) -> dict | None:
    """Dispatch one queued op through the same service functions the owner bot uses."""
    from app.security.service import (
        blacklist,
        extend_quarantine,
        forward_to_shop,
        lift_quarantine,
        remove_bypass,
        set_bypass,
    )

    identity = str(args.get("identity") or "").strip()
    if op == "llm_test":
        return await _llm_test()
    if not identity:
        raise ValueError("identity is required")

    if op == "quarantine_lift":
        await lift_quarantine(redis, identity)
    elif op == "quarantine_extend":
        await extend_quarantine(redis, identity)
    elif op == "blacklist":
        shop_id = args.get("shop_id")
        await blacklist(
            redis, identity, UUID(shop_id) if shop_id else None,
            str(args.get("reason") or "blacklisted by platform owner"), client,
        )
    elif op == "bypass_set":
        await set_bypass(redis, identity)
    elif op == "bypass_remove":
        await remove_bypass(redis, identity)
    elif op == "forward_to_shop":
        await forward_to_shop(redis, identity)
    else:
        raise ValueError(f"unknown op {op!r}")
    return None


async def drain_ops(redis: Any, client: Any | None = None) -> int:
    """Execute pending redis_ops rows. Returns how many were processed (applied OR errored).

    Each row is isolated: a failing op stamps its own `error` and the drain continues, so one
    malformed request can never wedge the queue.
    """
    try:
        sb = _sb(client)  # inside the guard: an unconfigured/unreachable DB must not raise here

        def _pending() -> list[dict]:
            return (
                sb.table("redis_ops").select("id,op,args")
                .is_("applied_at", "null").is_("error", "null")
                .order("created_at").limit(_MAX_OPS_PER_TICK).execute().data or []
            )

        rows = await asyncio.to_thread(_pending)
    except Exception:  # noqa: BLE001 — never let the outbox break the health beat
        logger.warning("console: redis_ops fetch failed", exc_info=True)
        return 0

    done = 0
    for row in rows:
        patch: dict[str, Any] = {}
        try:
            result = await _run_op(row["op"], row.get("args") or {}, redis, client)
            patch = {"applied_at": "now()", "result": result}
        except Exception as e:  # noqa: BLE001 — record why, move on
            logger.warning("console: op %s failed: %s", row.get("op"), e, exc_info=True)
            patch = {"error": str(e)[:500]}
        try:
            await asyncio.to_thread(
                lambda p=patch, i=row["id"]: sb.table("redis_ops").update(p).eq("id", i).execute()
            )
            done += 1
        except Exception:  # noqa: BLE001
            logger.warning("console: could not stamp redis_op %s", row.get("id"), exc_info=True)
    return done
