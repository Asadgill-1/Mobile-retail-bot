"""Product media upload → Supabase Storage (SPEC §4 step 10).

Bucket `shop-media` is private (migrations/002_storage_buckets.sql); the backend uploads
with the service-role key. Customer-facing links are time-limited signed URLs, generated
at send time — objects are never public.

Objects live at `{shop_id}/{product_id}/{filename}` so a shop's media is grouped and a
stray path can never land under another shop's prefix.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from app.core.config import settings

logger = logging.getLogger(__name__)

MAX_IMAGES = 5  # SPEC §4 step 10: "Upload images (up to 5), optional video"


def _sb(client: Any | None) -> Any:
    from app.db.supabase_client import get_supabase

    return client if client is not None else get_supabase()


def object_path(shop_id: UUID, product_id: UUID, filename: str) -> str:
    """Tenant-prefixed storage path. Filename is ours, never the customer's."""
    return f"{shop_id}/{product_id}/{filename}"


async def upload_media(
    shop_id: UUID,
    product_id: UUID,
    filename: str,
    data: bytes,
    content_type: str,
    client: Any | None = None,
) -> str:
    """Upload one object, return its storage path. supabase-py is sync → off the event loop."""
    sb = _sb(client)
    path = object_path(shop_id, product_id, filename)

    def _q() -> str:
        sb.storage.from_(settings.supabase_storage_bucket).upload(
            path, data, {"content-type": content_type, "upsert": "true"}
        )
        return path

    return await asyncio.to_thread(_q)


async def signed_urls(paths: list[str], ttl: int = 3600, client: Any | None = None) -> list[str]:
    """Time-limited download URLs for stored objects (private bucket). Used to show a customer a
    product's images/video on the Telegram channel. Bad paths are skipped, never fatal."""
    if not paths:
        return []
    sb = _sb(client)

    def _q() -> list[str]:
        bucket = sb.storage.from_(settings.supabase_storage_bucket)
        out: list[str] = []
        for path in paths:
            try:
                res = bucket.create_signed_url(path, ttl)
                url = res.get("signedURL") or res.get("signedUrl") or res.get("signed_url")
                if url and url.startswith("/"):
                    url = settings.supabase_url.rstrip("/") + url
                if url:
                    out.append(url)
            except Exception:
                logger.exception("signed url failed path=%s", path)
        return out

    return await asyncio.to_thread(_q)
