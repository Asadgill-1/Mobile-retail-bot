# Module: core

## Responsibility
Cross-cutting infrastructure: configuration (`config.py` re-exports `settings`), logging, and security helpers (internal API key, headers).

## Boundaries
- **Owns:** the single `settings` instance; logging setup; internal-API-key verification helper.
- **Exposes:** `app.core.config.settings`, `app.core.logging.setup_logging`, `app.core.security.*`.
- **Does NOT touch:** business logic, DB/Redis/LLM clients (those live in `db/`, `llm/`).

## Logging (§16 — Stage 12)
`setup_logging()` configures the root logger from `settings.log_level` with a structured line format
(`time level logger message`, `force=True` so it's idempotent). Called once per process at the two
entrypoints: `telegram_bot.run_all_polling` (bots) and `main.py` (API). A JSON formatter can slot in
here if a log aggregator ever needs it — deferred (`ponytail:`), no consumer yet.

## Key files
| Path | Role | Stage |
|------|------|-------|
| `config.py` | re-export `settings` singleton | 0 ✅ |
| `logging.py` | `setup_logging()` — structured logging | 12 ✅ |
| `security.py` | `verify_internal_api_key`, security headers | 1+ |

## Status
🟢 `config.py` + `logging.py` done. `security.py` per Stage 1+.
