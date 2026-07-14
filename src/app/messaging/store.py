"""Permanent chat-message archive (migration 009) — the shop-owner bot's 💬 Messages source.

Redis (escalations/context.py) stays the AI's working memory: last 25 turns, 24h TTL. This table
is the durable copy of every turn, written best-effort from the same choke point (`remember`).
Reading is shop-scoped (the shop-owner bot only queries his own shops); deleting is a
PLATFORM-owner-only action (owner bot 🧹 Messages menu).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

_ROLE_ICON = {"customer": "👤", "assistant": "🤖", "shopkeeper": "🧑‍💼"}


def _sb(client: Any | None) -> Any:
    from app.db.supabase_client import get_supabase

    return client if client is not None else get_supabase()


async def save_message(
    shop_id: UUID, identity: str, role: str, content: str, client: Any | None = None
) -> None:
    """INSERT one turn. Best-effort: a DB failure must never break the chat flow."""
    try:
        sb = _sb(client)

        def _q() -> None:
            sb.table("messages").insert(
                {"shop_id": str(shop_id), "identity": identity, "role": role, "content": content}
            ).execute()

        await asyncio.to_thread(_q)
    except Exception:
        logger.exception("message persist failed shop=%s identity=%s", shop_id, identity)


async def conversations(shop_id: UUID, limit: int = 10, client: Any | None = None) -> list[dict]:
    """Most recently active customer identities for one shop, newest first.

    ponytail: Python-side distinct over the last 200 rows; a DISTINCT ON RPC if volume demands.
    """
    sb = _sb(client)

    def _q() -> list[dict]:
        return (
            sb.table("messages")
            .select("identity,created_at")
            .eq("shop_id", str(shop_id))
            .order("created_at", desc=True)
            .limit(200)
            .execute()
            .data
            or []
        )

    rows = await asyncio.to_thread(_q)
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if row["identity"] not in seen:
            seen.add(row["identity"])
            out.append({"identity": row["identity"], "last_at": row["created_at"]})
            if len(out) >= limit:
                break
    return out


async def transcript(
    shop_id: UUID, identity: str, limit: int = 25, client: Any | None = None
) -> list[dict]:
    """Last `limit` turns of one conversation, oldest → newest."""
    sb = _sb(client)

    def _q() -> list[dict]:
        return (
            sb.table("messages")
            .select("role,content,created_at")
            .eq("shop_id", str(shop_id))
            .eq("identity", identity)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )

    rows = await asyncio.to_thread(_q)
    return list(reversed(rows))


async def delete_messages(
    shop_id: UUID | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    client: Any | None = None,
) -> int:
    """PLATFORM-owner delete: all / one shop / date range. Returns rows deleted (best known)."""
    sb = _sb(client)

    def _q() -> int:
        q = sb.table("messages").delete()
        if shop_id is not None:
            q = q.eq("shop_id", str(shop_id))
        if start is not None:
            q = q.gte("created_at", start.isoformat())
        if end is not None:
            q = q.lt("created_at", end.isoformat())
        if shop_id is None and start is None and end is None:
            # "delete ALL" still must carry a WHERE clause (PostgREST refuses a bare DELETE).
            q = q.gte("created_at", "1970-01-01T00:00:00+00:00")
        r = q.execute()
        return len(r.data or [])

    return await asyncio.to_thread(_q)


# --- pure formatters (Telegram HTML-safe via escape at the bot layer's discretion) ---


def format_conversations(shop_name: str, convs: list[dict]) -> str:
    """Header above the conversation buttons."""
    if not convs:
        return f"💬 {shop_name}: no saved conversations yet."
    return f"💬 {shop_name} — last {len(convs)} conversation(s). Tap one to read:"


def format_transcript(identity: str, rows: list[dict]) -> str:
    """One conversation, oldest → newest. Content clipped so Telegram's 4096 limit holds."""
    if not rows:
        return f"💬 {identity}: no messages saved."
    lines = [f"💬 Conversation with {identity} (last {len(rows)}):", ""]
    for row in rows:
        icon = _ROLE_ICON.get(row.get("role", ""), "❔")
        stamp = str(row.get("created_at", ""))[11:16]  # HH:MM from ISO timestamp
        content = str(row.get("content", ""))
        if len(content) > 200:
            content = content[:200] + "…"
        lines.append(f"{icon} {stamp}  {content}")
    return "\n".join(lines)


if __name__ == "__main__":  # self-check: python -m app.messaging.store
    rows = [
        {"role": "customer", "content": "hi, iPhone price?",
         "created_at": "2026-07-14T09:15:00+00:00"},
        {"role": "assistant", "content": "x" * 300, "created_at": "2026-07-14T09:15:05+00:00"},
        {"role": "shopkeeper", "content": "we can do 3400",
         "created_at": "2026-07-14T09:20:00+00:00"},
    ]
    t = format_transcript("+971501234567", rows)
    assert "👤 09:15  hi, iPhone price?" in t
    assert "…" in t and "x" * 201 not in t  # long content clipped
    assert "🧑‍💼 09:20  we can do 3400" in t
    assert format_transcript("+971501234567", []).endswith("no messages saved.")
    assert format_conversations("Shop 01", []).endswith("no saved conversations yet.")
    assert "Tap one" in format_conversations("Shop 01", [{"identity": "a", "last_at": "t"}])
    print("store self-check ok")
