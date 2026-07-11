#!/usr/bin/env bash
# Run all Telegram bots in long-polling mode (ADR-002, ADR-005):
# owner control bot + per-shop shopkeeper bot + per-shop customer bot.
# Runs against the LIVE Supabase DB (SupabaseTenantRepo). Per-shop bot tokens are
# read from shops.telegram_*_bot_token columns (seed via scripts/seed_shop_bots.py).
# For offline/local runs without Supabase, set MSC_USE_INMEMORY=1.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:-}:$(pwd)/src:$(pwd)/config:$(pwd)"
python -c "
import logging, os
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
from app.tenants.service import TenantService
from app.telegram_bot.bot import run_all_polling
if os.environ.get('MSC_USE_INMEMORY') == '1':
    from app.db.in_memory import InMemoryTenantRepo
    repo = InMemoryTenantRepo(); repo.seed_default()
    print('using InMemoryTenantRepo (MSC_USE_INMEMORY=1)')
else:
    from app.db.supabase_client import get_supabase, SupabaseTenantRepo
    repo = SupabaseTenantRepo(get_supabase())
    print('using SupabaseTenantRepo (live DB)')
run_all_polling(TenantService(repo))
"
