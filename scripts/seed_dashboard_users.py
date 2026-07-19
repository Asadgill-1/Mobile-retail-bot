#!/usr/bin/env python
"""Provision the dashboard's first logins (PLAN §3.2, migration 020).

Applies 020_dashboard_users.sql (idempotent), creates two Supabase Auth users via the
GoTrue admin API, and maps them in dashboard_users:

  keeper1@shop.local        -> role=keeper, shop 1 (first shop of Client A)
  owner@techstore.local     -> role=owner,  Client A (sees both its shops)

Usage: PYTHONPATH=src:config python scripts/seed_dashboard_users.py <password>
Safe to re-run: existing users/rows are left as they are.
"""
from __future__ import annotations

import sys

import httpx

from app.core.config import settings
from mcp_servers.supabase_server import apply_migration_fn, execute_sql_fn

KEEPER_EMAIL = "keeper1@shop.local"
OWNER_EMAIL = "owner@techstore.local"


def _admin_headers() -> dict[str, str]:
    key = settings.supabase_service_role_key
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def _find_user(base: str, email: str) -> str | None:
    r = httpx.get(f"{base}/auth/v1/admin/users", headers=_admin_headers(),
                  params={"per_page": 100}, timeout=30)
    r.raise_for_status()
    for u in r.json().get("users", []):
        if u.get("email") == email:
            return u["id"]
    return None


def _ensure_user(base: str, email: str, password: str) -> str:
    existing = _find_user(base, email)
    if existing:
        print(f"auth user exists: {email}")
        return existing
    r = httpx.post(f"{base}/auth/v1/admin/users", headers=_admin_headers(),
                   json={"email": email, "password": password, "email_confirm": True}, timeout=30)
    r.raise_for_status()
    print(f"auth user created: {email}")
    return r.json()["id"]


def main() -> int:
    if len(sys.argv) != 2 or len(sys.argv[1]) < 8:
        print("Usage: seed_dashboard_users.py <password (min 8 chars, used for both users)>",
              file=sys.stderr)
        return 2

    password = sys.argv[1]
    base = settings.supabase_url.rstrip("/")

    result = apply_migration_fn("020_dashboard_users.sql")
    print(f"migration 020 applied ({result['statements']} statements)")

    shops = execute_sql_fn(
        "select s.id, s.client_id from public.shops s "
        "join public.clients c on c.id = s.client_id "
        "where c.name like 'Client A%' order by s.created_at limit 1"
    )
    if not shops:
        print("no Client A shop found — seed the core schema first (001_init.sql)", file=sys.stderr)
        return 1
    shop_id, client_id = shops[0]["id"], shops[0]["client_id"]

    keeper_uid = _ensure_user(base, KEEPER_EMAIL, password)
    owner_uid = _ensure_user(base, OWNER_EMAIL, password)

    execute_sql_fn(
        "insert into public.dashboard_users (user_id, role, shop_id) "
        f"values ('{keeper_uid}', 'keeper', '{shop_id}') on conflict (user_id) do nothing"
    )
    execute_sql_fn(
        "insert into public.dashboard_users (user_id, role, client_id) "
        f"values ('{owner_uid}', 'owner', '{client_id}') on conflict (user_id) do nothing"
    )
    print(f"mapped: {KEEPER_EMAIL} -> keeper of shop {shop_id}")
    print(f"mapped: {OWNER_EMAIL} -> owner of client {client_id}")
    print("done — log in on the dashboard with these emails + your password.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
