"""Audit trail (SPEC §16). Append-only `audit_logs`: who did what, when.

Best-effort by design: an audit write must never break the action it records — a failed insert
is logged and swallowed, exactly like `escalations`/`security` notifications. Written from the two
Telegram command wrappers (`owner_only`, `keeper_command`), which are the only choke points where
every privileged action passes through *with* its actor.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


def _sb(client: Any | None) -> Any:
    from app.db.supabase_client import get_supabase

    return client if client is not None else get_supabase()


async def record(
    actor: str,
    action: str,
    *,
    shop_id: UUID | None = None,
    detail: dict | None = None,
    client: Any | None = None,
) -> None:
    """Append one audit row. Never raises — a failed audit (even a broken client) must not fail
    the audited action."""
    try:
        sb = _sb(client)

        def _q() -> None:
            sb.table("audit_logs").insert(
                {
                    "actor": str(actor),
                    "action": action,
                    "shop_id": str(shop_id) if shop_id else None,
                    "detail": detail or {},
                }
            ).execute()

        await asyncio.to_thread(_q)
    except Exception:
        logger.exception("audit write failed action=%s actor=%s", action, actor)


async def recent(limit: int = 15, shop_id: UUID | None = None, client: Any | None = None) -> list[dict]:
    """Most recent audit rows for `/owner audit` (§12/§16)."""
    sb = _sb(client)

    def _q() -> list[dict]:
        q = (
            sb.table("audit_logs").select("actor,action,shop_id,detail,created_at")
            .order("created_at", desc=True).limit(limit)
        )
        if shop_id is not None:
            q = q.eq("shop_id", str(shop_id))
        return q.execute().data or []

    return await asyncio.to_thread(_q)
