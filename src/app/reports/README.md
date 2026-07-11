# Module: reports

## Responsibility
Profit reports, owner dashboards, system health reporting (SPEC §6, §12, §13). Formatting (monospace, emojis, AED) lives here; data comes from `orders/`, `tenants/`, `security/`.

## Boundaries
- **Owns:** report composition + formatting (monospace, emojis, AED) + the health checker.
- **Exposes:** `parse_period(arg)`, `format_profit(summary, label)`, `format_owner_profit(items, label)`; `check_health(redis, repo)`, `format_health(report)`, `HealthReport`.
- **Does NOT touch:** profit math (in `orders/`); raw counts come from owning modules.

## Health (§13) — one checker, two callers
`check_health(redis, repo) -> HealthReport` probes DB (`repo.health_check()`), Redis (`ping`), LLM
(`is_configured`, not a live ping — `ponytail:`), Twilio (mocked till Stage 13), Celery workers
(`control.ping()`), and counts active conversations (`session:*`) + quarantined (`quarantine:*`). It
**never raises**. `ok` gates only on DB+Redis+Celery. Both `GET /health` (`main.py`) and the 60s
`health_check` beat task (`tasks/tasks.py`) call it — the beat pages the owner via `send_to_owner`.

## Key files
| Path | Role | Stage |
|------|------|-------|
| `service.py` | `parse_period` + `/profit` and `/owner profit` formatting (§6) | 8 ✅ |
| `health.py` | `check_health` + `format_health` (§13) — the one checker | 10 ✅ |

## Status
🟢 profit formatting + health live. `/owner dashboard|health|escalations|security|audit` on the owner
dispatcher (`telegram_bot/bot.py`) — dashboard/escalations/security read `orders`/`escalations`/`security`
accessors; `audit` is honest-empty (no `audit_logs` writer till Stage 12).

**Tail (Stage 10):** `/owner usage` (Redis-today + `usage_daily`-past merge), `/owner shop <id>` full
dashboard, `/report daily|inventory_low|top_products` (§12), `/productstats` (Q-014). Spec ref: §6, §12, §13.
