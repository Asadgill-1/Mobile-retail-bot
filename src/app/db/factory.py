"""Pick the TenantRepo backend by env — one place the bot runner, the Celery
worker, and any future entrypoint all agree on.

`MSC_USE_INMEMORY=1` → in-memory repo (offline/tests); otherwise the live
Supabase repo. Mirrors the selection currently inlined in scripts/run_bot.sh
(which may adopt this later — left untouched for now).
"""

from __future__ import annotations

import os

from app.db.base import TenantRepo


def get_tenant_repo() -> TenantRepo:
    if os.environ.get("MSC_USE_INMEMORY") == "1":
        from app.db.in_memory import InMemoryTenantRepo

        repo = InMemoryTenantRepo()
        repo.seed_default()
        return repo
    from app.db.supabase_client import SupabaseTenantRepo, get_supabase

    return SupabaseTenantRepo(get_supabase())
