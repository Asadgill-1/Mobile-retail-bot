# PONYTAIL-DEBT — Deferred-shortcut ledger

> Harvested from `ponytail:` comments in the codebase (see `docs/13-ENGINEERING-ETHOS.md` §D).
> Format: `<file>:<line>, <what was simplified>. ceiling: <limit>. upgrade: <trigger to revisit>.`
- Re-run harvest: `grep -rn "ponytail:" src/ tests/ migrations/ config/ mcp_servers/` and update this file at each stage end.

---

## Current ledger

- `src/app/escalations/service.py:29`, a freeze self-expires after 7 days. ceiling: a forgotten escalation silently hands the customer back to the AI mid-conversation. upgrade: an owner report of stale escalations (Stage 10) so they are closed deliberately, not by timeout.
- `src/app/telegram_bot/notify.py:35`, a fresh `Bot` per send (one HTTP session each). ceiling: wasteful under load. upgrade: hold long-lived `Bot` instances once notification volume justifies it.
- `src/app/reports/health.py`, LLM check = `is_configured` (config state), not a live ping. ceiling: a provider outage with valid creds reads "configured". upgrade: a cheap models-list ping if outages need catching (a live chat every 60s would burn tokens).
- `src/app/security/service.py:10`, blacklist hot-path truth lives in Redis (`blacklist:{id}`, no TTL); the DB row is the durable/audit copy. ceiling: a Redis flush drops the hot-path block until re-set. upgrade: rehydrate Redis from `blacklisted_phones` on startup (Stage 10/12) if a flush ever bites.
- `src/app/security/service.py:36`, `/quarantine_extend` re-arms to a fixed 24h. ceiling: not owner-configurable. upgrade: accept a duration arg if the owner asks.
- `src/app/security/detectors.py:31`, base64 detection is a naive 40+ char run. ceiling: false-positives on real tokens/URLs. upgrade: decode + inspect only if abuse shows up.
- `src/app/orders/service.py:19`, fetch the range's orders and aggregate profit in Python (mirrors search.py). ceiling: O(orders in range) per report. upgrade: a Postgres RPC/materialized view once a shop does thousands of orders a day.
- `src/app/products/search.py:137`, fetch the shop's in-stock rows and rank in Python. ceiling: O(catalogue) per message. upgrade: a Postgres RPC using the existing GIN indexes on `specs`/`tags` once a shop outgrows a few hundred products.
- `src/app/products/addproduct_flow.py:7`, `ConversationHandler` keeps `/addproduct` draft state in process memory, brushing SPEC §11. ceiling: single bot process — a restart loses in-flight drafts. upgrade: PTB persistence backed by Redis once the bot runs more than one process.
- `src/app/tasks/tasks.py:42`, pipeline reply computed but not sent back over WhatsApp. ceiling: no Twilio outbound. upgrade: Stage 13 sends `result.reply` via the `whatsapp/` outbound client.
- `migrations/001_init.sql:268`, RLS policies are permissive (`using (true)`) scaffold. ceiling: no per-request tenant context enforcement at DB layer. upgrade: tighten to `shop_id = current_setting('app.shop_id')::uuid` once request-tenant wiring lands.
- `config/settings.py:52`, per-shop bot tokens in `TELEGRAM_SHOP_BOTS_JSON` env (test/offline path). ceiling: live onboarding flow not built yet. upgrade: owner onboarding command writes tokens into `shops` rows directly.
- `tests/customer_simulator/userbot.py:17`, `Userbot` is a skeleton. ceiling: no e2e customer flow (needs real `.session` files). upgrade: Stage 3 build-out + `login.py` one-time phone login.
- `src/app/tasks/celery_app.py` (beat_schedule), usage-flush runs **hourly** and drains completed days only. ceiling: a >2-day `celery_beat` outage loses a day (the usage counter's 2-day TTL expires it before the next successful flush). upgrade: an ops alarm on beat liveness (the Stage-10 health check), or lengthen the counter TTL, if a multi-day outage ever bites.
- `src/app/messaging/pipeline.py` (process_message), session lock release is a plain `DEL`, no owner token. ceiling: if processing outlives the 30s lock TTL, another message can re-acquire the lock and this `DEL` frees that one too. upgrade: compare-and-delete with a per-hold token (Lua) if a session can legitimately run >30s.
- `src/app/messaging/pipeline.py` (process_message), a lock-contended message returns `locked` and is dropped. ceiling: fine on Telegram (sequential per bot), but the Twilio/Celery path would drop it. upgrade: `self.retry` on the `locked` action once the Twilio producer is live (Stage 13).
- `src/app/core/logging.py`, structured logs are a key=value line format, not JSON. ceiling: not machine-parseable by a log aggregator without a regex. upgrade: a JSON `Formatter` subclass (one class, no dep) when a log pipeline exists to consume it.

## Tags

- `no-trigger`: none currently.

## Stats

- 17 markers. **Stage 7 resolved 2** (pipeline stubs). **Stage 8 added 1** (orders Python aggregation). **Stage 8b/9** added 0 code markers (shortcuts tracked in ADR-010 / Q-006). **Stage 10 resolved 1** (`main.py` `/health` placeholder — now a real checker) and **added 2** (hourly usage-flush >2-day-outage ceiling; health LLM = config-check not live ping). **Stage 11 added 2** (session-lock plain-DEL release; `locked`→`self.retry` deferred to Stage 13). **Stage 12 added 1** (key=value logs, JSON formatter deferred). 0 with no trigger.

> The debt ADR-009 created — *"every failure promises a specialist that nobody is told about"* — is **paid** (Stage 6). Stage 7 closed the pipeline's two remaining stubs. **Stage 10 closed the usage-counter leak** — `celery_beat` now drains `usage:*` → `usage_daily` on the hourly flush; the counters are no longer written-and-expiring. No step in the SPEC §9 pipeline is a placeholder anymore except step 1 (Twilio sig-verify, dormant until the WhatsApp cutover at Stage 13).
