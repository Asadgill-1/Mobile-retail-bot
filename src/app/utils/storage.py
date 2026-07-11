"""Report upload → private `shop-reports` bucket + 24h signed URL (SPEC §10).

Separate from `products/media.py`: that one is product-scoped (`shop/product/file`) and
never signs (nothing sent media until Stage 13). Exports need the opposite — a signed URL
now, for the shopkeeper to download the .xlsx. supabase-py is sync → off the event loop.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from app.core.config import settings

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_TTL = 24 * 60 * 60  # 24h signed URL (SPEC §10)


def _sb(client: Any | None) -> Any:
    from app.db.supabase_client import get_supabase

    return client if client is not None else get_supabase()


async def upload_report(shop_id: UUID, filename: str, data: bytes, client: Any | None = None) -> str:
    """Upload one report under the shop's prefix, return a 24h signed download URL."""
    sb = _sb(client)
    path = f"{shop_id}/{filename}"  # tenant-prefixed, like media paths

    def _q() -> str:
        bucket = sb.storage.from_(settings.supabase_reports_bucket)
        bucket.upload(path, data, {"content-type": _XLSX, "upsert": "true"})
        res = bucket.create_signed_url(path, _TTL)
        url = res.get("signedURL") or res.get("signedUrl") or res.get("signed_url")
        if url and url.startswith("/"):  # some client versions return a path, not an absolute URL
            url = settings.supabase_url.rstrip("/") + url
        return url

    return await asyncio.to_thread(_q)
