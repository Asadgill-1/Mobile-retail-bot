#!/usr/bin/env python
"""Provision the PLATFORM owner's console login (repo owner-dashboard-mobile).

The platform owner is the SaaS operator — the person who sells this system to shops. That is a
different identity from a shop owner (who runs one client's shops via the shop dashboard), so it
gets its own auth user and never appears in `dashboard_users`: the console authorises by the
OWNER_EMAILS env allowlist, not by a role row.

Usage: PYTHONPATH=src:config python scripts/seed_platform_owner.py <email> <password>
Safe to re-run: an existing user just has its password reset.
"""
from __future__ import annotations

import sys

import httpx

from app.core.config import settings


def _headers() -> dict[str, str]:
    key = settings.supabase_service_role_key
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def main() -> int:
    if len(sys.argv) != 3 or len(sys.argv[2]) < 8:
        print("Usage: seed_platform_owner.py <email> <password (min 8 chars)>", file=sys.stderr)
        return 2
    email, password = sys.argv[1], sys.argv[2]
    base = settings.supabase_url.rstrip("/")

    users = httpx.get(
        f"{base}/auth/v1/admin/users", headers=_headers(), params={"per_page": 200}, timeout=30
    ).json().get("users", [])
    existing = next((u for u in users if u.get("email") == email), None)

    if existing:
        r = httpx.put(
            f"{base}/auth/v1/admin/users/{existing['id']}",
            headers=_headers(), json={"password": password}, timeout=30,
        )
        r.raise_for_status()
        print(f"password reset for existing platform owner: {email}")
    else:
        r = httpx.post(
            f"{base}/auth/v1/admin/users", headers=_headers(),
            json={"email": email, "password": password, "email_confirm": True}, timeout=30,
        )
        r.raise_for_status()
        print(f"platform owner created: {email}")

    print(f"Now set OWNER_EMAILS={email} in the console's env (Vercel + .env.local).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
