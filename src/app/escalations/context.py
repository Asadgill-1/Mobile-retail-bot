"""Conversation memory in Redis (SPEC §3 handover context, §7 last-25, §11 zero local memory).

Every turn is recorded — customer, AI, and shopkeeper alike — so that:
  - the AI is multi-turn instead of answering each message in isolation,
  - `/handover` needs no "restore" step at all: the history was never lost,
  - Stage 7 can snapshot the last 25 messages into `security_incidents`.

Roles: "customer" | "assistant" | "shopkeeper". The shopkeeper speaks *as the shop*, so on
replay both "assistant" and "shopkeeper" become assistant turns — the AI picks up a
conversation a human was holding, without being told a human held it.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

_SESSION_KEY = "session:{shop_id}:{identity}"

SESSION_MAX = 25  # SPEC §7: security_incidents captures the last 25 messages
SESSION_TTL_SECONDS = 86_400  # a conversation older than a day is a new conversation


def session_key(shop_id: UUID, identity: str) -> str:
    return _SESSION_KEY.format(shop_id=shop_id, identity=identity)


async def remember(redis: Any, shop_id: UUID, identity: str, role: str, content: str) -> None:
    """Append one turn, keep only the last SESSION_MAX, refresh the TTL."""
    key = session_key(shop_id, identity)
    await redis.rpush(key, json.dumps({"role": role, "content": content}))
    await redis.ltrim(key, -SESSION_MAX, -1)
    await redis.expire(key, SESSION_TTL_SECONDS)
    try:  # permanent archive (migration 009) — best-effort, never breaks the chat flow
        from app.messaging.store import save_message

        await save_message(shop_id, identity, role, content)
    except Exception:
        logger.exception("message persist failed shop=%s identity=%s", shop_id, identity)


async def history(redis: Any, shop_id: UUID, identity: str, limit: int = SESSION_MAX) -> list[dict]:
    """Oldest → newest. Returns [] for a fresh conversation."""
    raw = await redis.lrange(session_key(shop_id, identity), -limit, -1)
    out = []
    for item in raw:
        try:
            out.append(json.loads(item))
        except json.JSONDecodeError:  # a poisoned entry must not kill the conversation
            logger.warning("dropping unparsable session entry shop=%s identity=%s", shop_id, identity)
    return out


async def sync_relay(redis: Any, shop_id: UUID, identity: str) -> None:
    """Pull dashboard-sent turns into the session (messages.relay_pending, migration 021).

    The web dashboard cannot reach this Redis, so its customer sends land in the archive
    flagged relay_pending; the next AI turn drains them here so the model knows what the
    shop already told the customer. Raw rpush, NOT remember() — these rows are already
    archived, remember() would duplicate them. Best-effort: never costs an answer.
    """
    try:
        from app.messaging.store import mark_relayed, pending_relay

        rows = await pending_relay(shop_id, identity)
        if not rows:
            return
        key = session_key(shop_id, identity)
        for row in rows:
            await redis.rpush(key, json.dumps({"role": row["role"], "content": row["content"]}))
        await redis.ltrim(key, -SESSION_MAX, -1)
        await redis.expire(key, SESSION_TTL_SECONDS)
        await mark_relayed([r["id"] for r in rows])
    except Exception:
        logger.exception("relay sync failed shop=%s identity=%s", shop_id, identity)


async def forget(redis: Any, shop_id: UUID, identity: str) -> None:
    await redis.delete(session_key(shop_id, identity))
