#!/usr/bin/env python
"""Apply a migration to the live Supabase project via the Supabase MCP tooling.

Usage: PYTHONPATH=src:config python scripts/apply_migration.py <migration_file>
e.g.  PYTHONPATH=src:config python scripts/apply_migration.py 001_init.sql

Uses mcp.supabase_server.apply_migration_fn (Management API + PAT). Idempotent
migrations (CREATE ... IF NOT EXISTS) are safe to re-run.
"""
from __future__ import annotations

import sys

from mcp_servers.supabase_server import apply_migration_fn, get_project_info_fn


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: apply_migration.py <migration_file>", file=sys.stderr)
        return 2
    info = get_project_info_fn()
    print(f"project: {info['project_ref']}  ({info['project_url']})")
    result = apply_migration_fn(sys.argv[1])
    print(f"applied: {result['applied']}  ({result['statements']} statements)")
    print(f"tables ({len(result['tables_after'])}): {', '.join(result['tables_after'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
