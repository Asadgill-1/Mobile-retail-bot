#!/usr/bin/env bash
# Launch all Telegram bots (owner + rider + shop-owner + per-shop keeper/customer) in
# long-polling mode against the LIVE Supabase DB, using this project's own venv (not
# whatever `python` resolves to on PATH — see scripts/run_bot.sh for the WSL/CI variant).
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="src;config;."
export PYTHONUTF8=1
.venv/Scripts/python.exe -c "
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
from app.tenants.service import TenantService
from app.telegram_bot.bot import run_all_polling
from app.db.supabase_client import get_supabase, SupabaseTenantRepo
repo = SupabaseTenantRepo(get_supabase())
print('using SupabaseTenantRepo (live DB)', flush=True)
run_all_polling(TenantService(repo))
"
