"""Supabase MCP server — ops tools for the project's Supabase DB.

Exposes list_tables / execute_sql / apply_migration / get_project_info as MCP
tools so any MCP client (Claude Desktop, ZCode, etc.) can operate Supabase.
The tool functions are also plain-callable (used by `scripts/apply_migration.py`
and tests) without spinning up an MCP transport.

SQL runs via the Supabase Management API:
    POST https://api.supabase.com/v1/projects/{ref}/database/query
authorized by the account-level PAT (`SUPABASE_MGMT_TOKEN`). Multi-statement
batches are supported; the endpoint returns the last statement's result set.

See ADR-007. Stdio transport: `python -m mcp.supabase_server` (or `mcp run`).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from app.core.config import settings

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MIGRATIONS_DIR = _REPO_ROOT / "migrations"
_MGMT_BASE = "https://api.supabase.com/v1/projects"


def _project_ref() -> str:
    """Extract the Supabase project ref from the project URL."""
    # https://<ref>.supabase.co  ->  <ref>
    url = settings.supabase_url.rstrip("/")
    ref = url.removeprefix("https://").removeprefix("http://").split(".")[0]
    if not ref:
        raise RuntimeError("SUPABASE_URL missing project ref")
    return ref


def _execute_sql(query: str) -> Any:
    """Run a SQL batch on the project via the Management API. Returns parsed JSON."""
    token = settings.supabase_mgmt_token
    if not token:
        raise RuntimeError("SUPABASE_MGMT_TOKEN not set (need account-level PAT for migrations)")
    url = f"{_MGMT_BASE}/{_project_ref()}/database/query"
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(url, headers={"Authorization": f"Bearer {token}"}, json={"query": query})
    if resp.status_code >= 400:
        raise RuntimeError(f"Supabase SQL error {resp.status_code}: {resp.text}")
    # 201 Created on success; body is the last statement's result rows (JSON array) or [].
    try:
        return resp.json()
    except ValueError:
        return resp.text


# --- plain tool functions (callable directly + via MCP) ---
def list_tables_fn() -> list[str]:
    rows = _execute_sql(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' ORDER BY table_name"
    )
    if isinstance(rows, list):
        return [r.get("table_name") for r in rows if isinstance(r, dict)]
    return []


def execute_sql_fn(query: str) -> Any:
    return _execute_sql(query)


def apply_migration_fn(filename: str) -> dict[str, Any]:
    """Read migrations/<filename> and execute it as one SQL batch."""
    path = _MIGRATIONS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"migration not found: {path}")
    sql = path.read_text(encoding="utf-8")
    _execute_sql(sql)
    tables = list_tables_fn()
    return {"applied": filename, "statements": sql.count(";"), "tables_after": tables}


def get_project_info_fn() -> dict[str, Any]:
    return {
        "project_ref": _project_ref(),
        "project_url": settings.supabase_url,
        "service_role_configured": settings.supabase_service_role_key.startswith("eyJ"),
        "anon_configured": settings.supabase_anon_key.startswith("eyJ"),
        "mgmt_token_configured": bool(settings.supabase_mgmt_token),
        "ai_provider": settings.ai_provider,
        "ai_model": settings.ai_model,
    }


# --- MCP server (stdio) ---
mcp = FastMCP("supabase")


@mcp.tool()
def list_tables() -> str:
    """List public tables in the project's Postgres DB."""
    return json.dumps(list_tables_fn())


@mcp.tool()
def execute_sql(query: str) -> str:
    """Run a SQL batch on the project (read or write). Returns the last result set."""
    return json.dumps(execute_sql_fn(query), default=str)


@mcp.tool()
def apply_migration(filename: str) -> str:
    """Apply a migration file from migrations/<filename> to the live DB."""
    return json.dumps(apply_migration_fn(filename), default=str)


@mcp.tool()
def get_project_info() -> str:
    """Return the configured Supabase project ref + URL + key status."""
    return json.dumps(get_project_info_fn(), default=str)


if __name__ == "__main__":
    mcp.run()
