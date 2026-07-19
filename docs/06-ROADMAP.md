# 06 — Roadmap

> Phased plan. Each stage ends in a working, handoff-able state.
> The *current* stage is tracked live in `07-CURRENT-STATE.md`.

## Stages

### Stage 0 — Foundations ✅ (this step)
- [x] Repo + context layer scaffolded
- [x] Spec archived → `docs/SPEC-source.md`
- [x] Context-layer docs populated from spec
- [x] ADR-001..004 written
- [x] Project structure scaffolded (`src/app/*`, `config/`, `migrations/`, `scripts/`, `tests/`)
- [x] Full DB schema + RLS (`migrations/001_init.sql`)
- [x] Foundational config (`.env.example`, `requirements.txt`, `Dockerfile`, `docker-compose.yml`, `config/settings.py`)
- [x] LLM client stub (`src/app/llm/llm_client.py`)
- [x] `src/app/main.py` + module READMEs

### Stage 1 — Tenants core (§1, §2; ADR-006)
- [x] Repo interface (`db/base.py`) + in-memory repo + Supabase/Redis factories
- [x] `tenants`: Client + Shop + Shopkeeper models, suspension state, owner-auth helper
- [x] Client layer (list/get/shops-by-client) + usage repo methods (ADR-006)
- [x] Module + tests (23 passing)
- [ ] Owner command Telegram wiring (→ Stage 2)

### Stage 2 — Telegram bot framework (§2, §5, §6, §12)
- [x] `telegram_bot/bot.py`: owner auth gate, command router, shopkeeper stubs (one factory for all §3/§5/§6/§10/§12 commands)
- [x] Owner commands wired: `/pauseshop` `/resumeshop` `/shopstatus` (via TenantService)
- [x] `build_application()` + long-polling runner + `scripts/run_bot.sh`
- [x] `tests/customer_simulator/userbot.py` Telethon skeleton
- [x] Module + tests (10 bot tests; 33 total green)
- [ ] Webhook mode + full PTB integration test (optional; long-polling covers testing)

### Stage 3 — WhatsApp webhook + message pipeline (§9, §1) ✅
- [x] `whatsapp`: Twilio webhook, signature verify, `To`→shop lookup (mocked till Stage 13, ADR-002)
- [x] `messaging`: SPEC §9 pipeline (7 steps; suspension/quarantine/bypass/usage live, blacklist/attack/LLM stubbed to Stages 4/7)
- [x] Return 200 immediately + Celery enqueue (`tasks/` skeleton; worker at Stage 10)
- [ ] Per-session Redis lock + MessageSid dedup → **deferred to Stage 11** (concurrency hardening; not needed for single-threaded Telegram testing)
- [x] **Redis usage counters** `INCR usage:{client_id}:{shop_id}:{day}:{metric}` (ADR-006)
- [x] Module + tests (WhatsApp mocked per ADR-002) — 48 total green

### Stage 4 — AI/LLM service (§3, §4) ✅
- [x] `ai`: anti-hallucination system prompt, function-calling, `search_products` (ranking in `products/search.py`), relevance×boost scoring, promotion logic (prompt text, ADR-008)
- [x] `llm`: `chat()` + function definitions + prompts; retry-once (SPEC §11)
- [x] Out-of-domain detection → escalation hook (`escalate_to_human` tool; Stage 6 wires the handover)
- [x] **Tested against Moonshot** (ADR-004) — live check: product q → `search_products`, refund → `escalate_to_human`
- [x] Module + tests — 60 total green, zero network calls in the suite
- [ ] Conversation history (Redis session) → **deferred to Stage 6/7** (handover context + last-25-messages capture)

### Stage 5 — Product inventory (§4, §5) ✅
- [x] `products/service.py`: `/boost` `/unboost` `/tag` `/untag` `/cleartags` `/feature` + **cross-shop tenant guard** + trust-boundary validation (tag whitelist)
- [x] Commands wired on the per-shop keeper bot; typed errors → safe replies
- [x] Fixed blocking sync-Supabase call inside `async def` in `search.py` (would stall all bots)
- [x] `/addproduct` 11-step conversational flow (PTB `ConversationHandler`)
- [x] Image (≤5) / video upload to Supabase Storage — `migrations/002_storage_buckets.sql` created the missing `shop-media` bucket
- [x] Live end-to-end verify on the real project (insert, media, cross-shop denial, cleanup)
- [ ] `/productstats` → **deferred to Stage 8** (no views/suggestions data; orders+profit not built) — Q-014
- [x] Module + tests (75 total green)

### Stage 6 — Escalation / handover (§3) ✅
- [x] `escalations`: `pending_escalations`, notify shopkeeper, freeze AI, `/reply` `/handover`
- [x] Redis conversation memory (`context.py`) — every turn recorded, so `/handover` needs **no restore step**
- [x] AI is now **multi-turn** (replays the session; shopkeeper turns replay as the shop's own voice)
- [x] `telegram_bot/notify.py` — owner / shopkeeper / customer sends, all best-effort
- [x] Pipeline step 4b (freeze) + bypass forward share one path — retired a Stage-7 marker
- [x] **ADR-009 debt paid**: failures really summon a human and really page the owner
- [x] Owner paged when no shopkeeper is reachable
- [x] Module + tests (111 total green; live-verified escalate→freeze→handover→resolve)

### Stage 7 — Security (§7, §8) ✅
- [x] `security/detectors.py`: 6 attack patterns (injection, sql, rapid-fire, cross-shop, admin-cmd, cred-probe) — **pure functions**
- [x] `security/service.py`: auto-quarantine (`quarantine:{id}`, 1h TTL), `security_incidents` last-25 snapshot (reuses `escalations.context.history()` — no second store), blacklist (Redis + DB), bypass
- [x] Owner investigation cmds: `/investigate` `/quarantine_extend` `/quarantine_lift` `/blacklist` `/forward_to_shop`
- [x] `/bypass_ai` `/bypass_remove` (§8)
- [x] **Pipeline wired:** step 3 (blacklist, silent), step 4 (quarantine, generic reply), step 6 (attack → quarantine + owner alert). Steps 4/5 refactored to call `security` so the key strings have one owner.
- [x] Module + tests (143 total green; detectors + service + pipeline-attack + owner-cmd registration)

### Stage 8 — Profit + reporting (§6, §12; ADR-006) — profit ✅
- [x] `orders`: `models.py` (pure `line_profit` + `ProfitSummary`), `service.py` (`create_order` tenant-guarded + `profit_summary` range aggregation, cost = cost_price × qty)
- [x] `reports/service.py`: `parse_period` + `format_profit` + `format_owner_profit` (monospace, emojis, AED)
- [x] `/profit [today|yesterday|weekly|monthly|YYYY-MM-DD]` (keeper) + `/owner profit [all|compare|shop <id>] [period]`
- [x] Module + tests (live-verified create_order → profit_summary → format → cleanup)
- [x] **Order-placement flow (Q-017 → ADR-010): hybrid AI-drafts / shop-confirms.** `place_order` tool + `/orders` `/confirmorder` `/rejectorder`; migration 003 (draft status, order_number, atomic `decrement_stock` RPC); inventory check at draft + atomic decrement at confirm; no premature customer message. Live-verified.
- [x] **Human-in-the-loop negotiation (ADR-010 rev.1).** `request_price` tool + `/approveprice` `/custom` `/denyprice` + per-shop `/negotiation on|off`; migration 004 (`shops.negotiation_enabled`, `price_requests`, drops `products.min_price`); AI has no discount authority — only a shop-approved price becomes a discount; off checked fresh. Live-verified request→custom→draft discount + off-blocks. **164 total green.**
- [ ] Owner reports **grouped by client** (ADR-006) + `/owner usage` from `usage_daily` → folded into Stage 10 (needs the beat flush).
- [ ] `/report daily|inventory_low|top_products` (§12), `/productstats` (Q-014), `update_status` → Stage 10 / when order flow lands.

### Stage 9 — Excel order export (§10) ✅
- [x] `utils/excel.py`: **pure** openpyxl builder (blue #2563EB header, thin borders, auto-width, frozen header) — §10 columns, RAM/Storage from `specs`, net selling price, `detailed` adds time/rider/instructions
- [x] `utils/storage.py`: upload to private `shop-reports` bucket + **24h signed URL**; `migrations/005_reports_bucket.sql` (applied live) + `settings.supabase_reports_bucket`
- [x] `orders.export_orders`/`export_rider` (+ `orders_for_export`/`rider_orders_for_export`, drafts excluded, `pending`=confirmed, reuses `parse_period`)
- [x] `/exportorders [today|yesterday|YYYY-MM-DD|pending|all] [detailed]` + `/exportrider <rider_id> [period]` (sorted by address) on the keeper bot
- [x] Module + tests (4 excel + `__main__` self-check; **168 total**); **live round-trip verified** (query→workbook→upload→download signed URL→valid xlsx→cleanup)
- [x] `/exportrider` now produces real data — `/assigndelivery` assigns `orders.rider_id` (Q-006 resolved, Stage 12b)

### Stage 10 — Reports + dashboards + health (§12, §13; ADR-006) — beat flush ✅
> **`celery_beat` now schedules the usage-flush.** This stage is where beat and the real `/health` are born. See `07-CURRENT-STATE.md` → "Missing infrastructure".
- [x] **`celery_app.conf.beat_schedule`** — `flush-usage-counters` wired (`crontab(minute=15)`, `conf.timezone="UTC"`). Health-check entry slots in next.
- [x] **Daily usage flush beat job** (`tasks.flush_usage`: `scan_iter usage:*` → `getdel` completed-day keys → `upsert_usage`; today's key skipped so overwrite-upsert can't double-count; ADR-006). Live-verified round-trip; billing data no longer expiring.
- [x] Celery Beat **60s health check** → owner alert via `send_to_owner` (`alert_owner`'s signature is shop/customer-scoped — wrong shape). `health_check` beat entry (`schedule: 60.0`).
- [x] **Real `/health`** (DB, Redis, LLM, Twilio, Celery workers, active convos, quarantined; 503 unhealthy). **One checker (`reports.health.check_health`), two callers** — the beat task and the endpoint. Worker liveness = `celery_app.control.ping()`. Live-verified with a real `-P solo` worker on Memurai.
- [x] Owner dashboards: `/owner dashboard` `/owner health` `/owner escalations` `/owner security` `/owner audit` (+ existing `/owner profit`). `audit` honest-empty (no `audit_logs` writer till Stage 12).
- [ ] Flower basic-auth: **already done** by `--basic-auth` in `docker-compose.yml:41`. No FastAPI route until a reverse proxy exists.
- [ ] Tail: `/owner usage` (needs Redis-today + `usage_daily`-past merge), `/owner shop <id>` full dashboard, shopkeeper `/report daily|inventory_low|top_products`, `/productstats` (Q-014).

### Stage 11 — Concurrency / reliability hardening (§11) ✅
- [x] Per-session Redis lock (`lock:session:{shop_id}:{identity}` `SET NX EX 30`, released in finally; `locked` on contention) + MessageSid dedup (`dedup:{sid}` `SET NX EX 300`), **lock-first/dedup-second** so a `locked` retry isn't deduped
- [x] LLM retry+fallback (already — llm_client retry-once + single `ESCALATION_REPLY`, ADR-009) · Celery reliability (`task_acks_late` + `worker_prefetch_multiplier=1`)
- [x] Hardening + tests (4 new, 181 total); live-verified on Memurai (held→locked, normal→freed, dup→dropped TTL300)
- [ ] Load test toward 300+ concurrent → **deferred to Stage 13**: needs the live Twilio producer (dormant now) + real infra; a fakeredis micro-bench proves nothing. `locked`→`self.retry` wiring lands with the producer.

### Stage 12 — Production hardening (§16, ADR-004) — core ✅
- [x] **Audit trail** — `audit/service.py` (`record`/`recent` → `audit_logs`, best-effort never-raise) wired at the `owner_only`+`keeper_command` wrappers (every privileged action logged with actor, one place); `/owner audit` reads it. Live-verified.
- [x] **Structured logging** — `core/logging.py` `setup_logging()` on bot + API entrypoints (replaced ad-hoc basicConfig).
- [x] Error handling: ADR-009 posture already holds (notify/pipeline/orchestrator/audit never raise) — no blind sweep.
- [ ] **Full pytest suite + CI** → deferred: repo is not a git repo; add `.github/workflows/ci.yml` (`pytest -q`) when it is.
- [ ] **Prod Docker config** → `Dockerfile`/`docker-compose.yml` exist; add an env override at deploy.
- [ ] **Switch `AI_PROVIDER=openai` (GPT-4o)** (ADR-004) → deploy-time env flip (abstraction ready; flipping now breaks live Moonshot — no OpenAI key).

### Stage 13 — WhatsApp deploy switch (§1, ADR-002)
- [ ] Activate real Twilio path (was mocked during Telegram-first testing)
- [ ] **This is where `celery_worker` finally gets a producer.** Until now `process_whatsapp_message` has zero callers.
- [ ] Twilio **outbound** client — the worker computes `result.reply` and currently throws it away (`ponytail:` marker, `tasks/tasks.py:42`)
- [ ] Owner live cutover checklist

### Stage 14 — Shop & Shop-Owner web dashboard (separate repo: `mobile-shop-and-shop-owner-dashboard`)
> Runs in parallel with this backend, not blocked on Stage 13. Full plan + phase list in that repo's `PLAN.md`. **All schema this dashboard needs lives in THIS repo's `migrations/` folder** — the dashboard repo carries no migrations of its own.
- [x] **P0** — migration 020 (`dashboard_users`) applied live; `scripts/seed_dashboard_users.py` provisions the first keeper + owner logins.
- [x] **P1** — Next.js scaffold, Supabase Auth, tenant scope (`lib/scope.ts`, mirrors `_own_shop`), read-only Home/Orders/Inventory/Chats/Riders/Reports, dark mode, mobile bottom nav.
- [x] **P2** — all mutations as server actions, each porting its Python service twin exactly (same guards, same atomic `decrement_stock` RPC, same audit action codes so the owner bot's activity log humanizes dashboard actions for free): order confirm/reject/advance/assign/cancel, price approve/counter/deny, product CRUD + media upload (signed URL → Storage direct), rider add + COD reconcile, negotiation toggle.
- [x] **AI-relay fix** (migration 021, `messages.relay_pending` + `escalations/context.py::sync_relay`) — closes the gap P2 shipped with: a dashboard-sent customer message now drains into the AI's Redis session on its next turn, no bridge required (Stage 12f, `07-CURRENT-STATE.md`).
- [x] **P3** — POS (counter sales) + UAE tax invoices + IMEI tracking (migration 022: extends the existing `counter_sales`, new `product_units`/`invoices`/`invoice_counters`, `shops` TRN fields, `products.barcode`). Upgraded from the original plan after UAE regulation + niche-POS market research (Consumer Protection Law, FTA simplified/full invoice threshold, 2027 e-invoicing mandate scope, IMEI as the top differentiator among cell-phone-shop POS systems). Repair tickets and trade-ins deliberately deferred to a later phase per owner decision.
- [ ] **P4** — owner-only Oversight (cancellations/discounts/activity/cross-shop transcripts) + a bridge API on **this** FastAPI app (`/internal/escalations/reply|handover`, `/internal/export/orders|rider`) exposed via Cloudflare Tunnel — needed because escalation reply/handover and Excel export touch Redis/openpyxl, which only run on this backend's process.
- [ ] **P5** — polish: empty/error/loading states everywhere, AED + Dubai-TZ sweep, dark-mode contrast audit.

## Definition of "done" per stage

A stage is done when:
- Its checkboxes are complete,
- `07-CURRENT-STATE.md` reflects reality,
- Tests pass (or failing tests explicitly logged),
- The project builds/runs,
- `.context/HANDOFF.md` is updated for the next LLM,
- Any new folder is registered in `08-FILE-MAP.md`.

## Current stage

👉 See `docs/07-CURRENT-STATE.md`.
