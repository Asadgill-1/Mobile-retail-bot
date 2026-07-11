#!/usr/bin/env python
"""Populate shops.telegram_*_bot_token columns on the live DB from settings.

Reads TELEGRAM_SHOP_BOTS_JSON and writes each shop's keeper/customer bot tokens
+ test chat id onto the matching live shops row. Maps by whatsapp_number
(dev seed: shop1 -> +10000000001, shop2 -> +10000000002).

Uses the service-role REST path (RLS bypass). Safe to re-run (idempotent UPDATE).
Run:  PYTHONPATH=src:config python scripts/seed_shop_bots.py
"""
from __future__ import annotations

import asyncio

from app.core.config import settings
from app.db.supabase_client import get_supabase

# dev-seed whatsapp_number per shop_key (see migrations/001_init.sql)
NUMBER_BY_KEY = {"shop1": "+10000000001", "shop2": "+10000000002"}


async def main() -> int:
    sb = get_supabase()
    bots = settings.shop_bots
    if not bots:
        print("TELEGRAM_SHOP_BOTS_JSON not set; nothing to seed.")
        return 1
    for cfg in bots:
        key = cfg.get("shop_key")
        number = NUMBER_BY_KEY.get(key)
        if not number:
            print(f"skip {key}: no whatsapp_number mapping")
            continue
        sb.table("shops").update(
            {
                "telegram_keeper_bot_token": cfg["keeper_token"],
                "telegram_customer_bot_token": cfg["customer_token"],
                "telegram_customer_chat_id": cfg["customer_chat_id"],
            }
        ).eq("whatsapp_number", number).execute()
        print(f"updated {key} -> {number}")
    r = (
        sb.table("shops")
        .select("name, whatsapp_number, telegram_keeper_bot_token, telegram_customer_bot_token, telegram_customer_chat_id")
        .order("name")
        .execute()
    )
    print("--- shops after ---")
    for row in r.data:
        k = (row.get("telegram_keeper_bot_token") or "")[:10]
        c = (row.get("telegram_customer_bot_token") or "")[:10]
        print(f"  {row['name']} | {row['whatsapp_number']} | keeper={k}.. customer={c}.. chat={row.get('telegram_customer_chat_id')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
