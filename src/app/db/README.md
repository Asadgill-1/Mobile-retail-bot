# Module: db

## Responsibility
Async clients for Supabase (Postgres + Storage) and Redis, plus the **TenantRepo** abstract interface and an in-memory implementation. Single shared instances for the process.

## Boundaries
- **Owns:** Supabase + Redis connection lifecycle; the `TenantRepo` interface (`base.py`).
- **Exposes:** `get_supabase()`, `get_redis()`, `TenantRepo`, `InMemoryTenantRepo`, `SupabaseTenantRepo`.
- **Does NOT touch:** business logic; tenant scoping is enforced by RLS (ADR-003) + callers passing `shop_id`.

## Key files
| Path | Role | Stage |
|------|------|-------|
| `base.py` | abstract `TenantRepo` interface | 1 ✅ |
| `in_memory.py` | in-memory repo (tests/dev, seeded like 001_init.sql) | 1 ✅ |
| `supabase_client.py` | `get_supabase()` factory + `SupabaseTenantRepo` (real; exercised once Q-003 resolves) | 1 ✅ |
| `redis_client.py` | async Redis factory + `set_redis_for_test()` (fakeredis) | 1 ✅ |

## Status
🟢 Stage 1 interface + in-memory + factories done. Supabase real path untested until Q-003.
