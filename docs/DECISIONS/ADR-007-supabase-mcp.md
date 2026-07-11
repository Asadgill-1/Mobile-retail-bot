# ADR-007 — Supabase MCP server for DB ops

- **Status:** Accepted
- **Date:** 2026-07-07
- **Deciders:** owner
- **Stage when decided:** 2 (after live Supabase creds landed, Q-003)

## Context

Q-003 resolved: a real Supabase project (`uwlczgwlkqlflflpveeykj` → `uwlczgwlkqlflpveeykj`) with a service-role JWT, an anon JWT, and an account-level PAT (`sbp_…`). The PAT authorizes the Supabase **Management API**, including `POST /v1/projects/{ref}/database/query` which runs SQL batches (verified: single + multi-statement, returns the last result set, HTTP 201). Applying migrations and inspecting the live DB by hand (curl / Supabase Studio) is tedious and not repeatable for an LLM handoff.

## Options considered

### Option A — `psql` / `supabase db push` CLI
- Pros: official tooling.
- Cons: needs the DB direct-connection string + password (not provided), and `supabase` CLI install on every handoff machine. Not callable as a tool by an MCP-aware agent.

### Option B — A small MCP server exposing SQL/migration tools
- Pros: any MCP client (Claude Desktop, ZCode, Cursor) can list tables, run SQL, apply migrations through one contract; the same functions are plain-callable in-process (scripts, tests). No DB password needed — uses the PAT + Management API.
- Cons: a dependency on the `mcp` SDK in dev; one extra module to maintain.

### Option C — Hand-rolled REST scripts only, no MCP
- Pros: fewer moving parts.
- Cons: every agent re-derives the curl; no standard tool surface; not discoverable by MCP clients.

## Decision

Adopt **Option B**: a Supabase MCP server at `mcp_servers/supabase_server.py` (FastMCP, stdio transport). Tools: `list_tables`, `execute_sql(query)`, `apply_migration(filename)`, `get_project_info`. SQL runs via the Management API `database/query` endpoint with the PAT. Migration files live in `migrations/`; `apply_migration` reads and executes them as one batch.

The tool functions are plain-callable (not bound to the MCP transport), so `scripts/apply_migration.py` reuses `apply_migration_fn` to push migrations in-process — proving the path without an MCP client.

## Rationale

Owner directive ("make a mcp for supabase and apply migration"). Standard tool surface for any future LLM/agent to operate Supabase without re-deriving HTTP calls. Uses the PAT we already hold (no new secret). The plain-callable design keeps a single source of truth for SQL execution (no MCP-vs-script drift).

## Consequences

- Positive: repeatable migrations; live DB inspectable by agents; one SQL-execution path; `001_init.sql` applied and verified (12 tables + seed + RLS on the live project).
- Negative: `mcp` SDK is a dev dependency; **must install core `mcp` only, NOT `mcp[cli]`** — the `[cli]` extra pulls `starlette>=1.x` which breaks the runtime's `fastapi` (`starlette<0.42`) pin. Recorded in `requirements-dev.txt`.
- Follow-ups: tighten RLS policies (permissive scaffold — `ponytail:` marker in `001_init.sql`); wire the MCP server into a client config when an agent needs live DB access mid-task.

## Related

- ADR-003 (Supabase RLS), ADR-001 (stack), `mcp_servers/supabase_server.py`, `mcp_servers/README.md`, `scripts/apply_migration.py`, `migrations/001_init.sql`.
