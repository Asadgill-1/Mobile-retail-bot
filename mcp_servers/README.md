# mcp/ — Supabase MCP server

Ops tools that let an MCP client (Claude Desktop, ZCode, Cursor, …) operate the
project's Supabase DB: list tables, run SQL, apply migrations, read project info.
See ADR-007.

## Tools

| Tool | What it does |
|------|--------------|
| `list_tables` | List public tables in the project's Postgres. |
| `execute_sql(query)` | Run a SQL batch (read or write); returns the last result set. |
| `apply_migration(filename)` | Apply `migrations/<filename>` to the live DB. |
| `get_project_info` | Return project ref + URL + which keys are configured. |

SQL runs via the Supabase **Management API** (`POST /v1/projects/{ref}/database/query`),
authorized by the account-level PAT (`SUPABASE_MGMT_TOKEN` in `.env`). The PAT is
NOT the service-role JWT — it's the `sbp_…` token that authorizes management calls.

## Run as an MCP server (stdio)

```bash
pip install mcp                              # already in requirements-dev.txt
PYTHONPATH=src:config python -m mcp.supabase_server
```

Wire into a client (example for a stdio MCP client config):

```json
{
  "mcpServers": {
    "supabase": {
      "command": "python",
      "args": ["-m", "mcp.supabase_server"],
      "cwd": "<repo-root>",
      "env": { "PYTHONPATH": "src:config" }
    }
  }
}
```

The `.env` (gitignored) supplies `SUPABASE_URL`, `SUPABASE_MGMT_TOKEN`, etc.

## Use directly (no MCP client)

The tool functions are plain-callable — `scripts/apply_migration.py` uses
`apply_migration_fn` to push a migration in-process:

```bash
PYTHONPATH=src:config python scripts/apply_migration.py 001_init.sql
```
