# HANDOFF — Resume Brief

> **Read this first if you're an LLM picking up the project.** 1-page "where are we" summary. Full detail in `docs/`.

**Project:** Multi-Shop Chatbot — 30-shop WhatsApp + Telegram chatbot, AI anti-hallucination, intrusion detection, profit reporting, Excel export.
**Last touched:** 2026-07-10
**Stage:** 12 — production hardening ✅ core (§16: audit logging + structured logging). Next = Stage 13 (WhatsApp/Twilio cutover). Deferred: CI + `AI_PROVIDER=openai` flip (deploy-time).
**Health:** 🟢 Stages 0–12 core complete (Stage 10 §12 tail minor). **185 tests passing, zero network calls in the suite**, 5 Telegram bots polling live. SPEC §9 pipeline fully live. **Orders exist** (AI drafts → `/confirmorder` atomic decrement → customer told). **Excel export live.** **`celery_beat` runs two jobs.** **Real `/health` + owner dashboards.** **§11 hardening live** (per-session lock + MessageSid dedup + Celery acks_late). **§16 audit trail live** — every privileged owner/keeper command writes `audit_logs` via the command wrappers; `/owner audit` reads it. **Structured logging** on bot + API. All live-verified against the DB + real Redis + a real Celery worker on Memurai. Real Redis local (Memurai :6379). Loop: `/addproduct` → live Supabase → customer → pipeline → AI (search + place_order) → grounded reply; failures → a real human, only the owner learns (ADR-009).
**LLM:** official Moonshot `kimi-k2.6` direct (ADR-004 rev.2). `AI_TEMPERATURE` **must** be `1.0`.

**🟥 Blocker for end-to-end escalation testing:** the live `shopkeepers.telegram_id` is the `001_init.sql` seed placeholder `100000001`, so escalation notices reach **nobody** (verified live — it correctly fired the "no shopkeeper reachable" owner alert instead). Seed the real Telegram user ids, and have each shopkeeper press `/start` on their shop's **keeper** bot: a bot cannot message a user who has never started it.

---

## TL;DR

Stages 0–7 done. Foundations + tenants + 5 Telegram bots + live Supabase + the full customer path:
**customer message → `messaging/pipeline.py` (SPEC §9, all steps live) → `ai/orchestrator` (multi-turn, tool-calling) → `products/search.py` → grounded reply.**
Shopkeepers run `/addproduct` (11-step flow, media → `shop-media` bucket) and `/boost` `/tag` `/feature`.
Out-of-domain (refunds, "talk to a human") or **any system failure** → a real escalation: `pending_escalations` row, AI frozen, staff notified, `/reply` + `/handover`. The customer never learns a machine was involved; only the owner is paged (**ADR-009**).
**Stage 7 security:** the pipeline now defends itself — 6 attack patterns (`security/detectors.py`, pure), auto-quarantine (1h) + last-25 snapshot into `security_incidents` + owner alert, blacklist, and direct-to-shop bypass. Owner runs `/investigate` `/quarantine_lift` `/blacklist` `/forward_to_shop` `/bypass_ai` etc.

**168 tests green, zero network calls in the suite.** Live-verified against the real project and the real model.

## Key decisions (locked)

- **ADR-001** stack · **ADR-002** Telegram-first, WhatsApp mocked till Stage 13 · **ADR-003** Supabase RLS by `shop_id` (still a permissive scaffold — the app layer is what enforces isolation today) · **ADR-004 rev.2** LLM = **official Moonshot `kimi-k2.6` direct** (test) / OpenAI GPT-4o (prod) · **ADR-005 rev** 5 bots (owner control + per-shop keeper + customer); owner Telegram id `5215780245` · **ADR-006** `clients` above `shops` + `usage_daily` · **ADR-007** Supabase MCP server · **ADR-008 rev.2** AI tool surface: 4 tools (search / escalate / place_order / request_price), escalation by tool-call, `boost_level` never serialized, **price sorts ignore boost** · **ADR-009** the customer never meets the machine · **ADR-010** (+rev.1) hybrid booking: AI drafts, shopkeeper confirms; negotiation human-in-the-loop.
- **Engineering ethos (MANDATORY):** `docs/13-ENGINEERING-ETHOS.md` — caveman (terse prose, code normal), karpathy (think/surgical/simplicity/verifiable), ponytail (lazy ladder; `ponytail:` markers; root-cause fixes; leave one runnable check). Ledger: `PONYTAIL-DEBT.md` (13 markers). Stage handoff = ponytail-review the diff + refresh the ledger.

## What's done

- **Docs:** `SPEC-source.md` + `01`–`13` + ADR-001..009. `PONYTAIL-DEBT.md`.
- **Schema:** `001_init.sql` (12 tables, RLS, GIN indexes, seed) + `002_storage_buckets.sql` (`shop-media`) — both **applied live**.
- **Stage 1–2:** `db/` (TenantRepo + in-memory + Supabase + Redis + factory), `tenants/`, `telegram_bot/bot.py` (owner gate, `/pauseshop` `/resumeshop` `/shopstatus`, 5-bot runner).
- **Stage 3:** `messaging/pipeline.py` (channel-agnostic), `whatsapp/webhook.py` (Twilio sig-verify, mocked), `tasks/` Celery skeleton, Redis usage meter (ADR-006).
- **Stage 4:** `llm/` (`chat()` + tool schemas + prompts), `products/search.py` (SPEC §4 ranking), `ai/orchestrator.py`.
- **Stage 5:** `products/` service (tenant guard) + `addproduct_flow.py` + `media.py`; `tests/fixtures/catalog.py` + `scripts/seed_test_catalog.py` (8 scenario products on the owner's real photos).
- **Stage 6:** `escalations/{service,context}.py`, `telegram_bot/notify.py`, pipeline freeze step, multi-turn AI, `/reply` + `/handover`.
- **Stage 7:** `security/{detectors,service}.py` — 6 pure attack patterns + quarantine/blacklist/bypass/incident; pipeline steps 3/4/6 wired live; owner security commands. Key strings centralized in `security/service.py`.
- **Stage 8:** `orders/{models,service}.py` (pure `line_profit`, `create_order` tenant-guarded, `profit_summary` aggregation) + `reports/service.py` (`parse_period` + formatting); keeper `/profit`, owner `/owner profit`.
- **Stage 8b — hybrid booking (ADR-010):** `place_order` tool (AI drafts) → `orders.service.draft_order`; keeper `/orders` `/confirmorder` `/rejectorder`; migration 003 (`draft` status, `order_number`, atomic `decrement_stock` RPC). No premature customer message.
- **Stage 8c — human-in-loop negotiation (ADR-010 rev.1):** `request_price` tool + `/approveprice` `/custom` `/denyprice` + `/negotiation on|off`; migration 004 (`shops.negotiation_enabled`, `price_requests`, drops `products.min_price`). AI has no discount authority; only shop-approved prices apply.
- **Stage 9 — Excel export (§10):** `utils/excel.py` (pure openpyxl builder — §10 columns, `#2563EB` header, borders, auto-width, RAM/Storage from `specs`, net selling price) + `utils/storage.py` (upload → private `shop-reports` + 24h signed URL); `orders.export_orders`/`export_rider`; keeper `/exportorders [period] [detailed]` + `/exportrider <id> [period]`; migration 005 (shop-reports bucket). Full round-trip live-verified. Rider sheets empty until rider-assignment (Q-006).
- **Stage 10 — beat flush + health (§12/§13, ADR-006):** `celery_app.conf.beat_schedule` (usage-flush hourly + `health_check` 60s); `tasks.flush_usage` (drains completed-day `usage:*` → `usage_daily`); `reports/health.check_health` (one checker: DB/Redis/LLM/Twilio/Celery + convo/quarantine metrics) → real `GET /health` (503 unhealthy) + beat owner-page via `send_to_owner`; `repo.health_check()`; owner `/owner dashboard|health|escalations|security|audit` (+ `escalations.count_open`/`list_open`, `security.recent_incidents`). All live-verified (real Memurai worker start/stop flips celery ok).
- **Stage 11 — concurrency/reliability (§11):** `process_message` wraps the §9 body (`_dispatch`) in a per-session lock (`lock:session:{shop}:{id}` `SET NX EX 30`, released in finally, `locked` on contention) + MessageSid dedup (`dedup:{sid}` `SET NX EX 300`); **lock-first/dedup-second**; Celery `task_acks_late`+`prefetch=1`. Retry-once+fallback + 200-immediate were already done. Live-verified on Memurai.
- **Stage 12 — production hardening (§16):** `audit/service.py` (`record`/`recent` → `audit_logs`, best-effort never-raise) wired at the `owner_only`+`keeper_command` wrappers (every privileged action logged with actor, one place); `/owner audit` reads real rows; `core/logging.py` `setup_logging()` (structured lines) on bot + API entrypoints. Live-verified. CI + `AI_PROVIDER=openai` flip deferred (deploy-time).
- **Tooling:** Memurai (native Redis :6379, Windows service, auto-start). Docker Desktop installed but needs a reboot to finish (WSL2). Ponytail full skill installed globally (v4.8.4 + 6 slash-commands).
- **Live creds (gitignored `.env`):** 5 Telegram bot tokens, Supabase project `uwlczgwlkqlflpveeykj` (service-role + anon + mgmt PAT), Moonshot key. Every delivered credential file was consumed into `.env` and **deleted**.

## What's NOT done

- **Stage 13** (WhatsApp/Twilio cutover — activates `celery_worker` + outbound send + `locked`→`self.retry` + load test). Deploy-time: CI, `AI_PROVIDER=openai` flip.
- **Multi-item orders** (schema is one `product_id` per order) and **status beyond confirmed** (packed/shipped/delivered) — not built; revisit with delivery. Q-017 (order placement) is otherwise **resolved** (ADR-010).
- **Rider assignment** — `orders.rider_id` FK + `/exportrider` exist, but no flow sets a rider on an order, so rider sheets are empty (Q-006).
- `/report daily|inventory_low|top_products` (§12), `/productstats` (Q-014), client-grouped owner reports + `/owner usage` — Stage 10.
- ✅ Per-session Redis lock + MessageSid dedup — done (Stage 11), inline in `pipeline.py`.
- WhatsApp outbound send — Stage 13. Webhook + signature verify exist and are tested; only the cutover remains.
- RLS still permissive `using(true)` — the app layer enforces `shop_id` everywhere meanwhile.
- Proactive AI disclosure to customers — **Q-016**, open.
- ✅ **Usage leak CLOSED + health built (Stage 10).** `celery_beat` runs both `flush_usage_counters` (hourly) and `health_check` (60s → owner page); `/health` is a real checker (503 unhealthy). Remaining hollow-by-design: `celery_worker` still has zero callers (mocked WhatsApp webhook — Stage 13). §12 tail (`/owner usage`, `/owner shop`, shopkeeper `/report`, `/productstats`) is minor + partly data-blocked.

## Immediate next steps (Stage 13 — WhatsApp/Twilio cutover, §1, ADR-002)

1. Read `AGENTS.md`, `docs/07-CURRENT-STATE.md`, `docs/06-ROADMAP.md` (Stage 13), `docs/13-ENGINEERING-ETHOS.md`.
2. **Activate the real Twilio path** (mocked till now, ADR-002) — **this is where `celery_worker` finally gets a producer**. The webhook already sig-verifies + enqueues; flip the channel on.
3. **Twilio outbound client** — the worker computes `result.reply` and throws it away (`ponytail:` at `tasks.py:42`). Build the `whatsapp/` outbound send; `send_to_customer` in `notify.py` becomes the Twilio send (identity = phone). Wire `self.retry` on the pipeline's `locked` action (Stage 11 marker). Then the **300+-concurrent load test**.
4. **Deploy-time (Stage 12 tail):** flip `AI_PROVIDER=openai` (GPT-4o) + live re-verify; add `.github/workflows/ci.yml` once the repo is a git repo.
5. **Stage 10 §12 tail (optional):** `/owner usage` (merge **Redis-today** + `usage_daily`-past — the flush only drains completed days), `/owner shop <id>`, shopkeeper `/report daily|inventory_low|top_products`, `/productstats` (Q-014).

## Test catalogue

`scripts/seed_test_catalog.py` seeds 8 scenario products (+ the owner's real photos/videos from the gitignored `pices and Video/`) into the first active shop. Definitions live in `tests/fixtures/catalog.py`. Deterministic ids → re-run to update; `--clean` to remove.

```bash
PYTHONPATH="$(pwd)/src;$(pwd)/config;$(pwd)" python scripts/seed_test_catalog.py
```
Scenarios: same model/different spec · same model/different colour · same model/different condition · same brand/different model · different brand · different category · out-of-stock (must never be offered).

**Q-015 fixed (ADR-008 rev. 2).** The catalogue caught a live defect: *"what's your cheapest phone?"* → wrong answer, because search was boost-ranked + truncated with no price signal. The model hallucinated nothing — the tool lied by omission. Now: `sort=price_asc|price_desc` + `max_price_aed`, **price ordering ignores boost**, superlative rule in the prompt, `_SYNONYMS` for customer vocabulary. Read Q-015 + ADR-008 rev. 2 before touching `products/search.py`, `llm/functions.py`, or `llm/prompts.py`.

## Working with the live DB

The project ships its **own** Supabase MCP server: `mcp_servers/supabase_server.py` (ADR-007), authenticated by `SUPABASE_MGMT_TOKEN`. Its tool functions are plain-callable — no MCP transport needed:
```python
from mcp_servers.supabase_server import execute_sql_fn, apply_migration_fn, list_tables_fn
```
Use it to inspect the live project and to apply migrations. (The generic hosted Supabase connector returns "permission denied" — use this one.) It's how Stage 5 discovered the `shop-media` bucket had never been created.

Running a script that imports `config.settings` needs **project root** on `PYTHONPATH`, plus `PYTHONIOENCODING=utf-8` on Windows if you print non-ASCII.

## Stage 3–6 recap (what's live now)

- **The whole loop works on Telegram:** keeper bot `/addproduct` → live Supabase + `shop-media` → customer bot message → `messaging/pipeline.py` (SPEC §9) → `ai/orchestrator.answer_customer` → `search_products` → grounded reply. Run it: `docker compose up -d redis && bash scripts/run_bot.sh`.
- `products/` — `/addproduct` (11-step ConversationHandler), `/boost` `/unboost` `/tag` `/untag` `/cleartags` `/feature`, media upload. **Cross-shop tenant guard** in `service.get_product`.
- `messaging/pipeline.py` — channel-agnostic (`InboundMessage.identity` = telegram-id in test / phone in prod). Live: suspension, quarantine-read, bypass-read, usage meter, real AI. Stubbed: blacklist, attack-detect, bypass-forward (Stage 7).
- `ai/orchestrator.py` — chat loop + tool exec. **`boost_level` is never serialized to the model** (ADR-008). Escalation short-circuits. Never raises.
- `products/search.py` — SPEC §4 ranking, pure + tested. `llm/` — `chat()` w/ tool-calling + retry-once.
- `whatsapp/webhook.py` — sig-verify + 200-immediate, enqueues Celery. **Dormant until Stage 13.**
- `db/factory.py` — repo selection by `MSC_USE_INMEMORY`.

## Traps for the next LLM

- **Booking is AI-drafts / shop-confirms (ADR-010).** `place_order` writes a `draft`; only `/confirmorder` makes it real (status `confirmed`) and only then is stock decremented (atomic `decrement_stock` RPC) and the customer told. A `draft` is NOT revenue — `profit_summary` excludes it. Don't treat a draft as a placed order.
- **The AI has NO discount authority — negotiation is human-in-the-loop (ADR-010 rev.1).** There is no `min_price` floor (dropped in migration 004). When a customer haggles the AI calls `request_price`; a shopkeeper `/approveprice` / `/custom` / `/denyprice` decides; only a shop-**approved** price in `price_requests` becomes a discount, applied by `draft_order._approved_price`. `place_order` carries no price. Never reintroduce autonomous AI discounting.
- **The `/negotiation off` check is read FRESH from the DB every haggle** (`_negotiation_on`), not from the `shop` object — because the customer/keeper bots hold a startup snapshot of `shop`, and "off" is a money control that must take effect immediately. If you add other runtime shop toggles, do the same (or the snapshot goes stale until restart — the same latent issue affects `/pauseshop` on a running customer bot).
- **The customer is told NOTHING about an order until the shop confirms** (design #2). No "let me confirm that" message. Reject does not cold-message the customer. If you add customer messaging to the booking path, keep this — the order number appears only on `/confirmorder`.
- **A pending draft does NOT freeze the AI.** Unlike an escalation, the customer keeps chatting; a new order intent supersedes the old draft (cancels it). Don't add a freeze.
- **Profit cost = `cost_price × quantity`, not bare `cost_price`.** SPEC §6's literal formula omits the quantity; `cost_price` is per-unit and `selling_price` is the line total, so dropping the multiply under-counts cost on any multi-unit order — a silent money bug. Pinned by `orders/models.py` `__main__` + `test_line_profit_multiplies_cost_by_quantity`.
- **`/owner` is a dispatcher, not a leaf command.** `/owner profit ...` today; Stage 10 hangs `dashboard|escalations|security|audit|health` off the same `owner_cmd` in `bot.py`. Add subcommands there, don't make new top-level `/owner*` commands.
- **Excel export: builder is pure, service does the fetch, `Selling Price` = net.** `utils/excel.orders_workbook(rows, *, detailed)` takes the DB order shape (embedded `products`, and `delivery_persons` for rider sheets) and does ALL the flatten/style — no DB, unit-tested by reloading the bytes. `orders.orders_for_export` / `rider_orders_for_export` do the tenant-scoped query. **Drafts are never exported**; `pending` = `confirmed`. The `Selling Price` column is `selling_price − discount_amount` (what the customer pays), matching the confirm-message net. Reports upload to the **private** `shop-reports` bucket (migration 005) and return a 24h signed URL — never a public object. `/exportrider` works but is **empty** until something assigns `orders.rider_id` (Q-006); don't mistake an empty rider sheet for a bug.
- **The generic quarantine reply IS shown to the customer on purpose — that is not an ADR-009 violation.** SPEC §7 mandates "Your message could not be processed." for a *detected attacker*. ADR-009 protects *genuine* customers from meeting the machine; an attacker gets a deliberately flat reply. A false positive (a real customer tripping a detector) is recovered by the owner via `/quarantine_lift` or `/forward_to_shop` — which is exactly why the owner alert carries those commands. Do not "fix" this by routing attacks through the escalation reply.
- **The quarantine/bypass/blacklist Redis key strings have ONE owner: `security/service.py`.** The pipeline imports `is_quarantined`/`is_bypassed`/`is_blacklisted` — it must never re-inline `redis.exists("quarantine:...")`. Writer (owner commands, attack detector) and reader (pipeline) sharing one definition is the whole point.
- **`detectors.detect_attack` is pure — keep it that way.** Rapid-fire is the one volume-based pattern; the caller passes `msg_count_60s` (from `security.service.bump_rate`, a Redis 60s counter). Don't reach into Redis from `detectors.py`. A false positive quarantines a paying customer, so `tests/security/test_detectors.py` keeps a **clean set** of real shopping questions that must never match — extend it when you touch the patterns.
- **Blacklist hot-path truth is Redis, not Postgres.** `blacklist:{id}` (no TTL) is what the pipeline checks; `blacklisted_phones` is the durable/audit copy. A Redis flush drops the block until re-set (`ponytail:` marker) — rehydrate from the DB at Stage 10/12 if that ever bites.
- **A service existing in `docker-compose.yml` does not mean it does anything.** `celery_beat` now runs two real jobs (usage-flush + 60s health check) and `/health` is real (Stage 10), but `celery_worker` still holds exactly one task whose only caller is the *mocked* WhatsApp webhook (Stage 13). Read the code, not the compose file.
- **Celery tasks need a FRESH Redis per run — never the cached `get_redis()`.** A Celery task does `asyncio.run()` (a new event loop each firing); the cached async client binds to the first loop and then dies with `redis: down: Event loop is closed` on tick 2+. `_health_task`/`_flush_task` use `db.redis_client.new_redis()` and `await redis.aclose()` in `finally`. The bots are fine with the cached client (one persistent loop). This bug spammed the owner every 60s in the first live run.
- **The beat health check must NOT ping for workers (`include_celery=False`).** `control.ping()` from inside a busy **solo-pool** worker (Windows) returns 0 → false `celery: down: no workers` → owner spam. The beat task runs *on* a worker, so a worker is alive by construction; worker-liveness detection is the external `/health` endpoint's job (it and `/owner health` keep `include_celery=True`). Prod uses prefork (Linux) where ping works, but the beat still shouldn't self-ping.
- **Health: one checker, two callers — don't fork it.** `reports.health.check_health(redis, repo)` backs BOTH `GET /health` and the 60s `health_check` beat task. `ok` gates only on **DB + Redis + Celery workers**; LLM/Twilio are reported as config-state, not liveness (Twilio is mocked by design, so gating on it would page the owner forever; a live LLM ping every 60s would burn tokens — `ponytail:` swap in a models-list ping only if provider outages need catching). The beat pages the owner with **`send_to_owner`**, NOT `escalations.alert_owner` (that one is shop+customer-scoped — wrong shape for a system-wide failure). Worker count = `celery_app.control.ping()` in `_worker_count` (monkeypatch it in tests — no broker). `/owner audit` is honest-empty: nothing writes `audit_logs` until Stage 12.
- **Security is audited + hardened (`docs/SECURITY-AUDIT.md`, 2026-07-11) — don't regress these.** (1) Keeper bots are staff-gated: `_keeper_auth_gate` (TypeHandler group −10) allows only registered shopkeepers + owner, fails closed; never remove it or add keeper commands outside that gate. (2) RLS is locked (migration 006): RLS on every table, no policies, anon revoked — the data API is **service-role only**; don't re-add permissive policies or use the anon key. (3) Money bounds: `request_price` offer >0, `approve_price` `0<price≤list`. (4) Per-customer daily AI cap (`settings.ai_daily_msg_cap`, `security.bump_daily`). (5) `httpx`/`httpcore` logs are WARNING so bot tokens never hit logs — keep it. (6) `logger` is now defined in `orders/service.py` (was a latent NameError).
- **Shop→customer outcomes are recorded into the AI session, and `request_price` is idempotent.** Approvals/denials/confirmations are sent by the service (`send_to_customer`), NOT by the AI — so the AI never "hears" them unless we record them. `approve_price`/`deny_price`/`confirm_order` call `_remember_to_customer` (an assistant turn in the session) so the AI knows and doesn't re-ask or say "still waiting." And `request_price` is now idempotent: `already_approved` → returns `{"status":"already_approved"}` (prompt steers the model to `place_order`, which applies the approved price via `_approved_price`); an open `pending` request → reuse, no duplicate row (this is what made #3/#4 in testing). If you add other system→customer messages, remember them too, or the AI's picture goes stale.
- **The model gets a hidden product-id reference EVERY turn — don't remove it.** The session replays only text turns (for handover), so the real product UUIDs from an earlier `search_products` are gone by a later turn. Without help, the model invents an id for `place_order`/`request_price`/`show_product_media` (`prod_redmi_x11_blue`), the tool rejects it, and the turn dies as "empty model response" → false escalation + owner page. `orchestrator._id_reference(shop)` injects a "name → real id" system note (all in-stock) each turn. It's best-effort (never raises — runs before the escalation check) and must NEVER be shown to the customer (the note + prompt both forbid it). If you rework the session to replay tool results instead, you can drop this — until then it's what makes ordering reliable.
- **Customer media rides an out-param, not the return type.** `answer_customer(..., media_sink=None)` still returns a `str` (so all callers/tests stay string-typed); when the model calls `show_product_media`, the orchestrator appends `{"type","url"}` items to `media_sink`. `pipeline._dispatch` passes a list and surfaces it as `PipelineResult.media` (a tuple); the **channel adapter** sends it (customer bot `send_photo`/`send_video` now, Twilio MMS at Stage 13 — media is URLs, channel-agnostic). The orchestrator tool loop is **bounded to 3 rounds** so the model can search→show-media→answer; don't make it unbounded. URLs are 1h signed URLs from the private `shop-media` bucket (`products.media.signed_urls`).
- **The sales prompt is consultative, not a catalogue dump.** On a vague ask the AI must ask 1–2 qualifying questions (budget/brand/use), then show ≤3 matched options, and recommend when the customer is unsure. If you edit `llm/prompts.py`, keep that — the first live test showed the old prompt listing all 6 phones at once. And the prompt now says the AI *can* show photos (via `show_product_media`); don't reintroduce "I can't share photos / visit the store."
- **Audit is wired at the two command WRAPPERS, not per-command (§16).** `owner_only` and `keeper_command` call `_audit` in their `else`-on-success branch, so every privileged owner/keeper action is logged with its actor in one place — don't scatter `record()` into individual handlers. `audit.record` is **best-effort (never raises)** — an audit-write failure (even a broken client) must not break the action; keep it that way. It writes to `audit_logs` via `get_supabase()` directly (not through the repo), so **unit tests must stub it** (`tests/telegram_bot/test_bot.py` has an autouse `_no_audit` fixture) or the suite hits the network. `/owner audit` reads `audit.recent`.
- **§11 pipeline lock is lock-FIRST, dedup-SECOND — don't swap them.** `process_message` acquires `lock:session:{shop}:{id}` (`SET NX EX 30`) *before* the MessageSid dedup (`dedup:{sid}` `SET NX EX 300`). A lock miss returns `PipelineResult(None, "locked")` **before** the sid is marked seen — so the Celery `self.retry` the Twilio path will do at Stage 13 re-runs cleanly instead of being deduped away. If you dedup first, a contended retry gets silently dropped. The lock releases in `finally` (plain DEL — `ponytail:` marker: if a session ever runs >30s the DEL can free a re-acquired lock; upgrade to a token compare-del then). Telegram is sequential per bot (no `concurrent_updates`), so `locked` never fires on the live path today; it's for the Stage-13 Celery/Twilio producer, which must `self.retry` on `locked`. `lock_key`/`_is_duplicate` own the key formats — don't re-inline them.
- **The usage-flush drains COMPLETED days only — never today.** `tasks.flush_usage` skips any `usage:*` key whose day `>= today` (UTC). This is not an optimization, it's correctness: `repo.upsert_usage` **overwrites** the row's count, and today's counter is still incrementing, so draining it mid-day and deleting the key would lose every later message. If you ever need mid-day usage (a live dashboard), read Redis directly — do NOT flush today. The beat runs **hourly** (not once at midnight) so a missed tick self-heals; because only past days are touched, extra runs are idempotent no-ops. Key format + parse both live in `pipeline.py` (`USAGE_KEY_PREFIX`, `parse_usage_key`) — the writer owns them; don't re-inline the split in `tasks.py`.
- **supabase-py is SYNC.** Never call `.execute()` straight from an `async def` — it blocks the event loop and stalls all 5 bots. Always `await asyncio.to_thread(_q)` (see `SupabaseTenantRepo`, `products/search.py`, `products/service.py`, `products/media.py`). This bug shipped in Stage 4 and was caught in Stage 5.
- **Settings can point at infrastructure that doesn't exist.** `supabase_storage_bucket = "shop-media"` was set from Stage 0, but the bucket was never created — the first upload would have failed in production. Check the live project (via the MCP server) before trusting a setting. Buckets/DDL go in `migrations/`, never ad-hoc SQL.
- **`Decimal` crosses the wire as a string.** Postgres `numeric` returns `Decimal('1000.0')`, not `'1000.00'` — compare by value, never by `str()`.
- **The escalation freeze is per shop**, keyed `escalation:frozen:{shop_id}:{identity}`. `/reply` and `/handover` take the customer id as shopkeeper free text and refuse a customer not frozen *for that shop* — Shop B must never answer Shop A's escalated customer. Two tests pin it.
- **`escalations.context` is THE session store.** Every turn (customer / assistant / shopkeeper) is recorded as it happens, last 25 kept. That is why `/handover` needs no "restore" step, why the AI is multi-turn, and what Stage 7's `security_incidents` snapshot should read. Don't build a second one.
- **`freeze()` before `notify`** in `escalate()`. If notification is slow, the customer's next message must already miss the AI.
- **Notifications never raise.** `telegram_bot/notify.py` returns bool; `escalate`/`alert_owner`/`_to_humans` swallow and log. They run on the customer's reply path, often inside an `except` block.
- **Never write a customer-facing error message** (ADR-009). There is no `FALLBACK_REPLY` and there must never be one again. Every non-answer — escalation, outage, empty model response — returns the identical `ESCALATION_REPLY` through the single exit `ai/orchestrator._handoff_to_human()`. A crash must be byte-identical to a routine handoff. Only `_alert_owner()` learns what broke. Pinned by `test_no_fallback_reply_constant_survives`.
- **Never instruct the model to deny being an AI.** It doesn't volunteer it; if asked sincerely it hands off to a human. Denial would be deception and is regulated (see Q-016).
- **`kimi-k2.*` accepts only `temperature=1`.** Any other value → HTTP 400 → retry-once → `answer_customer` swallows it into `FALLBACK_REPLY`. Every customer message would fail while looking merely "flaky". `AI_TEMPERATURE=1.0` in `.env` and as the `settings.py` default. Use `moonshot-v1-*` if you need a lower temperature. Also: `api.moonshot.cn` 401s global keys, and `kimi-k2.7-code*` are code-specialised — not for customer chat.
- **Never let `boost_level` back into price sorting** (`products/search.py`). Boost promotes; it must never hide a cheaper product. That regression produced a confidently wrong "cheapest" answer (Q-015). Pinned by `test_cheapest_ignores_boost_the_q015_regression`.
- **A tool that returns a truncated, ranked slice cannot answer a superlative.** Any new tool with a result cap needs the same treatment: expose the ordering, and forbid the model from inferring "cheapest / only / none" without it.
- **Never weaken the product tenant guard.** `product_id` is shopkeeper free text and may name another shop's row. All mutations route through `service.get_product(shop_id, product_id)`; `ProductNotFound` deliberately uses the same message for "unknown" and "belongs to another shop". `tests/products/test_service.py` proves cross-shop denial.
- **Unit tests must never hit the network.** Pipeline step 7 calls the real AI; `answer_customer` swallows all exceptions into `FALLBACK_REPLY`, so a live call *passes silently* while adding ~2.5s. Any test that reaches step 7 must `monkeypatch.setattr(pipeline, "answer_customer", ...)` (see `tests/messaging/`, `tests/tasks/`).
- **Q-012 / Q-013 are open** (`12-OPEN-QUESTIONS.md`): SPEC §4's ranking formula contradicts itself, and SPEC §4 literally excludes brand/model from search. Both implemented with the sensible reading. **Don't silently re-decide them.**
- **Never add `boost_level` to `_serialize()`** — the leak guard is structural, and `tests/ai/test_orchestrator.py::test_serialize_hides_boost_level_but_keeps_tags` will catch you.
- Running a script from a non-root dir? `python file.py` puts the *script's* dir on `sys.path`, not cwd — `config.settings` then vanishes. Needs project root on `PYTHONPATH` (see `scripts/run_bot.sh`).

## Files the next LLM must read

- `AGENTS.md`
- `docs/07-CURRENT-STATE.md` · `docs/06-ROADMAP.md` (Stage 2) · `docs/11-API-CONTRACTS.md`
- `docs/12-OPEN-QUESTIONS.md`
- `docs/03-ARCHITECTURE.md` · `docs/10-DATA-MODEL.md`
- `src/app/tenants/auth.py` + `service.py` (the API Stage 2 calls)
- `migrations/001_init.sql`

## Run / test

```bash
pip install -r requirements.txt -r requirements-dev.txt
PYTHONPATH=src:config pytest -q   # 37 green
PYTHONPATH=src:config uvicorn app.main:app --reload   # /health
bash scripts/run_bot.sh           # runs all 5 bots against live Supabase DB (long-polling)
MSC_USE_INMEMORY=1 bash scripts/run_bot.sh   # offline variant (in-memory repo)
```

## Open questions

- ✅ resolved: Q-001/2 · Q-003 Supabase (`uwlczgwlkqlflpveeykj`, migrations applied) · Q-005 LLM key (now **official Moonshot**, ADR-004 rev.2) · Q-009 bot layout · **Q-015 price blindness** (ADR-008 rev.2).
- 🟡 open: **Q-012** boost as hard sort key vs relevance multiplier · **Q-013** are brand/model searchable · **Q-014** `/productstats` has no data source · **Q-016** proactive AI disclosure · Q-004 Twilio numbers (Stage 13) · Q-006 rider model (Stage 8) · Q-007 multi-currency · Q-008 migration tooling · Q-010/Q-011 client layer/billing.
- 🟥 **Seed real `shopkeepers.telegram_id` values** (currently the `100000001` placeholder) and have each shopkeeper `/start` their keeper bot — otherwise escalations reach nobody.
- **Create 2 Telethon `.session` files** before customer-simulator e2e tests.

## Do NOT

- Don't pin `httpx` (supabase needs 0.27.x — let it resolve).
- Don't hardcode the LLM provider — go through `src/app/llm/llm_client.py`.
- Don't store money as float (use `Decimal`).
- Don't query across shops without `shop_id` (RLS + app layer both enforce).
- Don't create loose root files — purpose-built folders, registered in `08-FILE-MAP.md`.
