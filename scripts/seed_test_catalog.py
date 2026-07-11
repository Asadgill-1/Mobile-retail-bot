"""Seed the realistic test catalogue into a shop (SPEC §4, §5 scenarios).

Uses the production code paths — `products.media.upload_media`, `products.service.create_product`,
`set_boost`, `add_tags`, `toggle_featured` — so seeding exercises exactly what `/addproduct`
and the keeper commands do. Ids are deterministic (uuid5), so re-running updates in place.

    python scripts/seed_test_catalog.py                 # seed the first active shop
    python scripts/seed_test_catalog.py --shop "Abu"    # match a shop by name/uuid/number
    python scripts/seed_test_catalog.py --clean         # remove every fixture row + its media

Needs project root on PYTHONPATH (see scripts/run_bot.sh) and a live Supabase in .env.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

from app.db.supabase_client import get_supabase
from app.products.media import upload_media
from app.products.service import add_tags, create_product, set_boost, toggle_featured
from tests.fixtures.catalog import CATALOG, MEDIA_DIR, ProductFixture

_BUCKET = "shop-media"
_CONTENT_TYPE = {".jpeg": "image/jpeg", ".jpg": "image/jpeg", ".mp4": "video/mp4"}


def _resolve_shop(needle: str | None) -> tuple[UUID, str]:
    rows = get_supabase().table("shops").select("id,name,whatsapp_number,status").execute().data
    if not rows:
        sys.exit("no shops in the database")
    if needle:
        for r in rows:
            if needle.lower() in (r["name"] or "").lower() or needle in (
                str(r["id"]),
                r.get("whatsapp_number") or "",
            ):
                return UUID(r["id"]), r["name"]
        sys.exit(f"no shop matching {needle!r}")
    for r in rows:  # default: first ACTIVE shop — a suspended shop answers no customers
        if r["status"] == "active":
            return UUID(r["id"]), r["name"]
    sys.exit("no active shop")


def _read(filename: str) -> tuple[bytes, str]:
    path = MEDIA_DIR / filename
    if not path.exists():
        sys.exit(f"missing media file: {path}")
    return path.read_bytes(), _CONTENT_TYPE[path.suffix.lower()]


def _purge(shop_id: UUID, product_id: UUID) -> None:
    """Delete the row and any storage objects under {shop_id}/{product_id}/."""
    sb = get_supabase()
    sb.table("products").delete().eq("id", str(product_id)).eq("shop_id", str(shop_id)).execute()
    prefix = f"{shop_id}/{product_id}"
    try:
        objects = sb.storage.from_(_BUCKET).list(prefix)
        if objects:
            sb.storage.from_(_BUCKET).remove([f"{prefix}/{o['name']}" for o in objects])
    except Exception:  # bucket empty / prefix absent — nothing to clean
        pass


async def _seed_one(shop_id: UUID, fx: ProductFixture) -> str:
    pid = fx.product_id(shop_id)
    _purge(shop_id, pid)  # idempotent re-seed

    image_paths = []
    for i, name in enumerate(fx.images):
        data, ctype = _read(name)
        image_paths.append(await upload_media(shop_id, pid, f"image_{i}.jpeg", data, ctype))
    video_path = None
    if fx.video:
        data, ctype = _read(fx.video)
        video_path = await upload_media(shop_id, pid, "video.mp4", data, ctype)

    await create_product(
        shop_id,
        product_id=pid,
        category=fx.category,
        brand=fx.brand,
        model=fx.model,
        color=fx.color,
        condition=fx.condition,
        specs=fx.specs,
        cost_price=fx.cost_price,
        selling_price=fx.selling_price,
        quantity=fx.quantity,
        images=image_paths,
        video_url=video_path,
    )
    # Apply promotion state through the same commands a shopkeeper would use.
    if fx.boost_level:
        await set_boost(shop_id, pid, fx.boost_level)
    if fx.tags:
        await add_tags(shop_id, pid, list(fx.tags))
    if fx.is_featured:
        await toggle_featured(shop_id, pid)

    media = f"{len(image_paths)} img" + (" + video" if video_path else "")
    return (
        f"  {fx.brand} {fx.model} · {fx.color} · {fx.condition} · "
        f"{fx.specs.get('storage', '—')} · qty {fx.quantity} · "
        f"boost {fx.boost_level} · tags {list(fx.tags) or '—'} · {media}\n"
        f"      ↳ {fx.note}"
    )


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shop", help="shop name substring, uuid, or whatsapp number")
    ap.add_argument("--clean", action="store_true", help="delete fixture rows + media, then exit")
    args = ap.parse_args()

    shop_id, shop_name = _resolve_shop(args.shop)
    print(f"shop: {shop_name}  ({shop_id})\nmedia: {MEDIA_DIR}\n")

    if args.clean:
        for fx in CATALOG:
            _purge(shop_id, fx.product_id(shop_id))
        print(f"removed {len(CATALOG)} fixture products + their media")
        return

    for fx in CATALOG:
        print(await _seed_one(shop_id, fx))
    print(f"\nseeded {len(CATALOG)} products into {shop_name}")


if __name__ == "__main__":
    asyncio.run(main())
