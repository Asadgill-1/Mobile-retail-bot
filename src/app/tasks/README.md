# Module: tasks

## Responsibility
Celery app, task definitions, and the Beat schedule. Tasks wrap slow/background + periodic work (SPEC §11, §13; ADR-006).

## Boundaries
- **Owns:** the Celery app instance, task registry, `beat_schedule` (inline in `celery_app.py` — it's ~15 lines of config, not a module).
- **Exposes:** `celery_app`; tasks `process_whatsapp_message` (dormant till Stage 13), `flush_usage_counters` (hourly beat), `health_check` (60s beat); testable cores `_run`, `flush_usage`, `run_health_check`.
- **Does NOT touch:** business logic — tasks are thin wrappers. The usage-key format + parser live in `messaging/pipeline.py` (the writer owns them); `flush_usage` imports them.

## Usage flush (ADR-006 — Stage 10)
`flush_usage(repo, redis, *, today=None)` drains **completed-day** `usage:*` Redis counters into
`usage_daily` (`getdel` + `upsert_usage`) and deletes the key. **Today's key is skipped** — `upsert_usage`
overwrites the count and today is still incrementing, so a mid-day drain would lose later messages.
Idempotent; malformed keys skipped. Beat runs it **hourly** (`crontab(minute=15)`, UTC) so a missed
midnight tick self-heals (past-days-only ⇒ extra runs are no-ops). Ceiling: a >2-day beat outage can
still lose a day (the counter's 2-day TTL) — an ops alarm, not a code fix.

## Key files
| Path | Role | Stage |
|------|------|-------|
| `celery_app.py` | Celery instance + config + `beat_schedule` (flush hourly + health 60s) | 10 ✅ |
| `tasks.py` | `process_whatsapp_message` (+ `_run`), `flush_usage_counters` (+ `flush_usage`), `health_check` (+ `run_health_check`) | 3 / 10 ✅ |

## Status
🟢 Stage 10 core done. **Beat runs two jobs** — hourly usage-flush + 60s health check (pages the owner via
`send_to_owner` on failure; the checker is `reports.health.check_health`, shared with `GET /health`).
`process_whatsapp_message` stays dormant until Stage 13 (Twilio path); Telegram testing runs the pipeline
inline. Spec ref: §11, §12, §13.
