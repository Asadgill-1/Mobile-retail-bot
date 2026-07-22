# Module: tasks

## Responsibility
Celery app, task definitions, and the Beat schedule. Tasks wrap slow/background + periodic work (SPEC §11, §13; ADR-006).

## Boundaries
- **Owns:** the Celery app instance, task registry, `beat_schedule` (inline in `celery_app.py` — it's ~15 lines of config, not a module).
- **Exposes:** `celery_app`; tasks `process_whatsapp_message` (dormant till Stage 13), `flush_usage_counters` (hourly beat), `health_check` (60s beat); testable cores `_run`, `flush_usage`, `run_health_check`; `heartbeat_forever` (the in-process substitute for Beat).
- **Does NOT touch:** business logic — tasks are thin wrappers. The usage-key format + parser live in `messaging/pipeline.py` (the writer owns them); `flush_usage` imports them.

## Usage flush (ADR-006 — Stage 10)
`flush_usage(repo, redis, *, today=None)` drains `usage:*` Redis counters into `usage_daily`.
A **completed day** is final: `getdel` + `upsert_usage`, so a re-run is a no-op. **Today** is read
without deleting and upserted as a running total — `upsert_usage` overwrites, so each pass just
replaces today's number and the first pass after midnight writes the true total. (Skipping today
entirely, as this did before Stage 12j, left the console blind to the current day.)
Idempotent; malformed keys skipped. Ceiling: a >2-day outage can still lose a day (the counter's
2-day TTL) — an ops alarm, not a code fix.

Metrics written by `messaging/pipeline.py`: `messages`, `ai_calls`, `tokens_in`, `tokens_out`.
The token counts are what the platform owner is billed for; the console prices them in AED.

## In-process heartbeat (Stage 12j)
`heartbeat_forever()` runs `_health_task()` every 60s and `_flush_task()` every 10th tick. The
bot process starts it (`telegram_bot/bot.py::_run_apps_forever`) and cancels it on shutdown.
Reason: the owner runs the bots alone on a PC with no Celery worker or Beat — without this the
console's health snapshot is never published (its strip reads "backend offline" while the bots
are up), queued console operations are never drained, and usage never reaches the DB. Both jobs
are idempotent, so a real Beat running alongside is harmless. A failing tick is logged and the
loop continues — a dead subsystem must not take the heartbeat down with it.

## Key files
| Path | Role | Stage |
|------|------|-------|
| `celery_app.py` | Celery instance + config + `beat_schedule` (flush hourly + health 60s) | 10 ✅ |
| `tasks.py` | `process_whatsapp_message` (+ `_run`), `flush_usage_counters` (+ `flush_usage`), `health_check` (+ `run_health_check`), `heartbeat_forever` | 3 / 10 / 12j ✅ |

## Status
🟢 Stage 10 core done. **Two periodic jobs** — hourly usage-flush + 60s health check (pages the owner via
`send_to_owner` on failure; the checker is `reports.health.check_health`, shared with `GET /health`).
Run by Celery Beat in Docker, and by `heartbeat_forever` inside the bot process everywhere else.
`process_whatsapp_message` stays dormant until Stage 13 (Twilio path); Telegram testing runs the pipeline
inline. Spec ref: §11, §12, §13.
