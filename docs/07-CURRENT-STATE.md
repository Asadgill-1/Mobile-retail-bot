# 07 — Current State

> **Most important file for an incoming LLM.** Says exactly where the project is.
> Update at the end of every work session, before handoff.

**Last updated:** 2026-07-19
**Current stage:** Stage 12 core + full live QA audit + delivery/rider/COD + **shop-owner bot (7th bot)** + **inline-button UX + security audit suite (all bots)** + **gap-fix wave (6 phases + migration 010)** + **Stage 12e: web dashboard P1+P2 (separate repo, migration 020)** + **Stage 12f: dashboard→AI Redis relay (migration 021) + dashboard P3 POS/invoices/IMEI (migration 022)** + **Stage 12g: dashboard P4 (Shop logs + chat reply/handover + CSV exports + analytics), bridge API eliminated**. Next = Stage 13 (WhatsApp/Twilio cutover) — the dashboard is feature-complete through P4.
**Stage #:** 12
**Overall health:** 🟢 Stages 0–12 core complete + full 8-goal live QA audit + delivery lifecycle (fulfilment status, rider onboarding, custody handshake, COD ledger) + a global **shop-owner bot** (remote oversight for clients owning 1+ shops, ADR-006) + a **permanent chat archive** (`messages` table, dual-written alongside the Redis session) + **inline buttons on every bot** (slash commands stay the primary entry, buttons are a second path into the same service calls) + a **live 6-phase gap-fix wave**: friendly reference codes (`PR0001`/`rider001`, migration 010) replacing raw UUIDs everywhere a human types one, real `/productstats` (was a stub), low-stock alerts, a printable counter-sale sheet, platform-owner onboarding (`/addclient`/`/addshop`/`/setshoptokens`/`/addkeeper` — previously repo-only, unreachable), owner analytics (top products/cancels+discounts/COD across all shops) + an escalation ✔️ Resolve button (`/reply` alone never closed a row), shop-owner date-range orders + a 📋 activity log (button presses are now audited, not just slash commands), and **counter (walk-in) sales** — a hand-filled sheet photographed by the shop owner, read by a vision model, confirmed by a human before anything is written (man-in-the-middle by design), folded into `/profit`. **502 tests passing** (no network in the suite); **7 bots** polling live (owner + per-shop keeper/customer + global rider bot + **global shop-owner bot**). SPEC §9 pipeline fully live. **Orders exist end to end**: AI drafts → `/confirmorder` → `/deliveryupdate`/rider delivery → `delivered`. **Excel export live** (rider sheets populated, plus a new printable counter-sale sheet). **`celery_beat` runs two jobs.** **Real `/health` + owner dashboards + owner analytics.** **§11 hardening live** (per-session lock + MessageSid dedup + Celery acks_late). **§16 audit trail live:** every privileged owner/keeper command writes `audit_logs` (command wrappers **and** the mutating inline buttons **and now the web dashboard's mutations**, via reused action codes); `/owner audit` and the shop-owner's 📋 Logs both read it. **Structured logging** (`core.setup_logging`) on both the bot and API processes. All live-verified against the DB + real Redis + a real Celery worker on Memurai — **including migration 010's live apply and a real vision-model round trip** (see Stage 12d below). Real Redis local (Memurai :6379).
**LLM:** official Moonshot `kimi-k2.6` direct (ADR-004 rev.2 — OpenRouter dropped) for chat; `moonshot-v1-32k-vision-preview` for counter-sale sheet reading (`AI_VISION_MODEL`, Stage 12d). **`AI_TEMPERATURE` must be `1.0`**: `kimi-k2.*` 400s on any other value, which would silently degrade every reply to the fallback message.

---

## ✅ Done

### Stage 0 — Foundations
- Spec archived → `docs/SPEC-source.md` (immutable); context-layer docs `01`–`12` populated.
- ADR-001 (stack), ADR-002 (Telegram-first), ADR-003 (Supabase RLS), ADR-004 (LLM abstraction), **ADR-005 (testing topology)**.
- Project scaffold: 15 modules under `src/app/`, each with `README.md`.
- `migrations/001_init.sql` — 10 tables, FKs, JSONB `specs`, `tags`, `boost_level`, DECIMAL money, indexes (GIN on specs/tags), RLS, dev seed.
- Foundational config: `.env.example`, `requirements.txt`, `requirements-dev.txt`, `Dockerfile`, `docker-compose.yml`, `config/settings.py`, `pyproject.toml`, `.gitignore`.
- LLM client stub (`src/app/llm/llm_client.py`); FastAPI app (`src/app/main.py`, `/health` ✅ boots).
- Testing topology locked: **5 bots** (owner control + per-shop shopkeeper + per-shop customer) + 2 Telegram accounts + Telethon userbot; `tests/customer_simulator/` harness. (ADR-005 revised — see below.)
- **Engineering ethos adopted** (`docs/13-ENGINEERING-ETHOS.md` + `PONYTAIL-DEBT.md`): caveman (terse prose), karpathy (surgical/simplicity/verifiable), ponytail (lazy ladder, `ponytail:` markers). Wired into `AGENTS.md` read-order + stage checklist + `05-CONVENTIONS.md`. 4 `ponytail:` markers placed (llm_client, supabase_client, main, RLS policies).

### Stage 1 — Tenants core (service layer; ADR-006 client layer)
- `db/base.py` — abstract `TenantRepo` interface (clients + shops + shopkeepers + usage).
- `db/in_memory.py` — `InMemoryTenantRepo` seeded like `001_init.sql` (2 clients, 3 shops, owner shopkeeper, shop 3 suspended).
- `db/supabase_client.py` — `get_supabase()` factory + `SupabaseTenantRepo` (real; exercised once Q-003 resolves).
- `db/redis_client.py` — async Redis factory + `set_redis_for_test()`.
- `tenants/models.py` — `Client`, `Shop` (with `client_id`), `Shopkeeper`, `ShopStatus`, `ShopStatusInfo`, `UsageDailyPoint`.
- `tenants/service.py` — `TenantService` (clients, suspend/resume/status, usage, is_suspended, resolve_shopkeeper).
- `tenants/auth.py` — `is_owner`, `require_owner`, `resolve_shopkeeper`.
- **ADR-006** — `clients` table above `shops` (multi-shop clients) + `usage_daily`; schema + seed updated in `001_init.sql` (pglast-validated).
- Tests: `tests/conftest.py`, `tests/db/test_in_memory_repo.py`, `tests/tenants/{test_service,test_auth,test_clients_usage}.py` — **23 passing**.
- Verified: `pytest` green; FastAPI `/health` returns 200.

### Stage 2 — Telegram bot framework (5 bots, multi-bot runner)
- `telegram_bot/bot.py` — `owner_only` auth gate; owner commands `/pauseshop` `/resumeshop` `/shopstatus` (wired via TenantService, resolve by UUID **or** whatsapp_number); `/start` `/help`; shopkeeper stubs (one factory, all §3/§5/§6/§10/§12 commands → "not implemented (Stage N)").
- **ADR-005 revised → 5 bots:** owner control bot (`build_application`) + per-shop `build_shopkeeper_application` (staff side, scoped to one shop) + per-shop `build_customer_application` (customer-facing channel; Stage 3 pipeline stub echoes back). `run_all_polling(service)` runs all of them under one event loop (PTB manual init/start/start_polling + graceful stop).
- **Real bots wired & live:** `TELEGRAM_BOT_TOKEN` = owner control bot; `TELEGRAM_SHOP_BOTS_JSON` = per-shop keeper+customer tokens + test chat ids. `Shop` model + `migrations/001_init.sql` carry `telegram_keeper_bot_token` / `telegram_customer_bot_token` / `telegram_customer_chat_id` columns. `InMemoryTenantRepo.seed_default()` attaches tokens from settings (positional, testing only — ponytail marker for DB upgrade at onboarding).
- Smoke-tested: all 5 bots `getMe`+`getUpdates` 200 OK, no 401/409.
- `scripts/run_bot.sh` → `run_all_polling`. `tests/customer_simulator/userbot.py` — Telethon `Userbot` skeleton.
- Tests: `tests/telegram_bot/test_bot.py` — 14 tests (owner cmds, auth gate, stubs, help, app build, settings.shop_bots JSON parse, seed token attachment, _build_all_applications yields 5). **37 total green.**
- ponytail-review: lean, no cuts.

> **Owner-provided bot tokens** were delivered as 3 root `.txt` files, wired into gitignored `.env`, then **the txt files were deleted** (no loose root files, no secrets committed).

### Stage 3 — WhatsApp webhook + message pipeline (SPEC §9, §11)
- `messaging/pipeline.py` — channel-agnostic SPEC §9 pipeline. `InboundMessage(shop, identity, text, message_sid)` → `process_message(msg, redis)` → `PipelineResult(reply, action)`. `identity` = Telegram user id (testing) / phone (prod) in one field, so Stage 13 swaps only the adapter. **Live steps:** 2 suspension (auto-reply, no metering), 4 quarantine-read (`quarantine:{identity}`), 5 bypass-read (`bypass_ai:{identity}`), 7 usage meter + AI-stub. **Stubbed (ponytail):** 3 blacklist + 6 attack (Stage 7), 5 forward-to-shopkeeper (Stage 7), 7 real LLM (Stage 4).
- **Usage meter (ADR-006):** `INCR usage:{client_id}:{shop_id}:{day}:messages` with a 2-day TTL safety net (Stage 10 beat flushes → `usage_daily`).
- `telegram_bot/bot.py` — customer bot `_customer_message` now routes inbound through `process_message` (echo stub removed). **This is the live test channel** (WhatsApp mocked, ADR-002).
- `whatsapp/webhook.py` — `POST /webhook/whatsapp`: real Twilio signature verify (`verify_twilio_signature`, unit-tested now per ADR-002) → `To`→shop lookup → enqueue `process_whatsapp_message.delay(...)` → **return 200 immediately** (SPEC §11). Mounted in `main.py`. Dormant until Stage 13 (no real numbers). Outbound send deferred to Stage 13 (ponytail).
- `tasks/celery_app.py` + `tasks/tasks.py` — Celery app (Redis broker) + `process_whatsapp_message` skeleton (`_run` = testable core: fetch shop → run pipeline). Worker + Beat wired at Stage 10.
- `db/factory.py` — `get_tenant_repo()` (Supabase live / in-memory by `MSC_USE_INMEMORY`) so the webhook/worker and bot runner agree on backend selection.
- Tests: `tests/messaging/test_pipeline.py` (4: ai+usage / suspended / quarantine / bypass), `tests/whatsapp/test_webhook.py` (5: sig-verify valid/invalid/no-token, route 403 / enqueue-200 / unknown-shop-200), `tests/tasks/test_tasks.py` (2: pipeline core / unknown shop). **48 total green.** App boots with `/webhook/whatsapp` mounted.
- Fixed pre-existing latent bug: deleted dead `TenantService.resolve_shopkeeper` (NameError — called an unimported free function; zero callers).

### Stage 4 — AI / LLM service (SPEC §3, §4, §5; ADR-008)
- `llm/llm_client.py` — `chat()` **implemented**: AsyncOpenAI against `settings.ai_base_url`, tool-calling, transport retry-once (SPEC §11). `_to_wire` / `_to_response` map both directions; unparsable tool arguments degrade to `{}` rather than crash.
- `llm/functions.py` — two tools: `search_products(requirements)`, `escalate_to_human(reason)`.
- `llm/prompts.py` — anti-hallucination + promotion system prompt (SPEC §3, §5), `FALLBACK_REPLY`, `ESCALATION_REPLY`.
- `products/models.py` — `Product` (money = `Decimal`, never float).
- `products/search.py` — **ranking algorithm (SPEC §4)**: `relevance = spec_matches + 2×tag_matches`; `score = relevance × (1 + boost/10)`; order `(score, is_featured, boost_level)` DESC. The tail keys implement SPEC §5 "vague request → featured first" for free. `search_products()` fetches the shop's in-stock rows (tenant-scoped) and ranks.
- `ai/orchestrator.py` — `answer_customer(shop, identity, message)`: chat → tool-call → execute → answer. **Escalation short-circuits** (no product search, no second round). Never raises — returns `FALLBACK_REPLY` on failure.
- **ADR-008** — 2 tools; escalation by tool-call (no keyword classifier); **`boost_level` is never serialized to the model** (what isn't sent can't leak), `tags` are (needed for §5 phrasing). Money crosses as a string.
- `messaging/pipeline.py` step 7 now calls the real AI service (`_ai_stub_reply` deleted).
- **Live-verified against Moonshot** (`moonshotai/kimi-k2`): product question → `search_products{requirements:"Samsung phone with a good camera"}`; "I want a refund" → `escalate_to_human{reason:"refund request"}`. Both SPEC §3 rules hold on the real model.
- Tests: `tests/products/test_search.py` (7: relevance weighting, boost multiplier, boost-can't-beat-a-much-better-match, vague→featured, brand searchable, limit), `tests/ai/test_orchestrator.py` (5: boost-leak guard, search round-trip, escalation short-circuit, plain answer, fallback). **60 total green.**
- Fixed: `tests/tasks/test_tasks.py` was silently making a real LLM call once step 7 went live (passed only because `answer_customer` swallows errors). Now stubbed — **suite makes zero network calls**.
- ponytail-review on own diff: cut `_MAX_TOOL_ROUNDS` (config that never changes), a `for _ in range(1)` loop, and two unused `_run_tool` params. Planned `ai/spec_search.py`, `ai/promotion.py`, `ai/escalation_detect.py` were **never written** — duplicate / prompt-text / tool-call respectively.

### Stage 5 (part 1/2) — Product commands (SPEC §5)
- `products/service.py` — `get_product` (**the tenant guard**), `set_boost`, `add_tags`, `remove_tag`, `clear_tags`, `toggle_featured`. Typed errors: `ProductNotFound`, `InvalidBoostLevel`, `InvalidTag`.
- **Tenant guard is the security control of this stage.** `product_id` comes from shopkeeper free text, so every mutation resolves through `get_product(shop_id, product_id)` — one guard, all callers route through it. Mutations *also* re-filter `.eq("shop_id", …)` at the DB layer (belt-and-braces: RLS is still a permissive scaffold). Cross-shop reads and writes both raise `ProductNotFound`, with the **same message** as an unknown id — never confirm another shop's product exists.
- Validation at the trust boundary: `parse_boost_level` (1–10), `parse_tags` (normalize, de-dupe, **whitelist** against SPEC §5's 11 tags — a typo like `clearence` would silently never promote).
- `telegram_bot/bot.py` — `/boost` `/unboost` `/tag` `/untag` `/cleartags` `/feature` wired on the **per-shop keeper bot** (they need `bot_data["shop"]`); `product_command` decorator maps typed errors → safe replies. Owner control bot keeps the stubs. Keeper `/help` rewritten to show what actually works.
- **Bug fixed (introduced Stage 4):** `products/search.py` called the *sync* supabase-py SDK directly inside `async def` — blocking I/O on the event loop would have stalled all 5 bots on every customer message (CONVENTIONS anti-pattern). Now wrapped in `asyncio.to_thread`, matching `SupabaseTenantRepo`. Both `search.py` and `service.py` accept an injected `client` for tests.
- `/productstats` retargeted Stage 5 → **Stage 8** — it needs views/suggestions tracking (no such table) plus orders/profit (Stage 8). Logged as **Q-014**, not silently stubbed.
- Tests: `tests/products/test_service.py` (9: boost/tag validation, cross-shop read denial, cross-shop mutation denial, tag union/no-op/clear/toggle), `tests/telegram_bot/test_bot.py` +1 (keeper bot registers real product handlers, not stubs). **70 total green.**
- ponytail-review on own diff: cut `clear_boost()` — it was `set_boost(..., 0)` with extra steps.

### Stage 5 (part 2/2) — `/addproduct` + media (SPEC §4)
- `migrations/002_storage_buckets.sql` — creates the private `shop-media` bucket. **It did not exist.** Found by querying the live project through `mcp_servers/supabase_server.py` (ADR-007) — `settings.supabase_storage_bucket` pointed at a bucket that was never created, so the first media upload would have failed. Applied to the live project via the same MCP server.
- `products/media.py` — `upload_media()` → `{shop_id}/{product_id}/{filename}`, tenant-prefixed so a stray path can't land under another shop. `MAX_IMAGES = 5`. `to_thread`-wrapped. **No signed-URL helper**: nothing sends media to a customer until Stage 13; it would have been dead code.
- `products/service.py` — `create_product()` (`shop_id` from the bot's shop, never user input; `Decimal` → string across the wire) + `new_product_id()` (client-side id, so media uploads to `{shop_id}/{product_id}/` *before* a single insert — one write, no update-after-insert) + field parsers: `parse_category`, `parse_condition`, `parse_price`, `parse_quantity`, `parse_spec_line`, `parse_non_empty`.
- `products/addproduct_flow.py` — SPEC §4's 11 steps on PTB's **`ConversationHandler`** (native state machine, nothing hand-rolled). `/cancel` at any step; invalid input re-asks rather than advancing; media collected as bytes and uploaded only on `/save`. A `_step()` factory covers the six identical validate-then-advance steps.
- `telegram_bot/bot.py` — flow registered on the keeper bot; `KEEPER_REAL_COMMANDS` keeps `/addproduct` out of the stub loop. `/help` updated.
- **Live-verified against the real project** (create → media upload → read → cross-shop read denied → cross-shop write denied → product untouched → cleanup). Cross-shop denial holds on the live DB — which matters, because RLS is still `using(true)`, so `service.get_product` is the *only* thing enforcing isolation today.
- Tests: `tests/products/test_service.py` +5 (category/condition canonicalization, `Decimal` price incl. NaN/Infinity rejection, quantity, spec-line parsing, `create_product` forces `shop_id` + stringifies money); `test_bot.py` updated (`/addproduct` is a ConversationHandler, not a stub). **75 total green.**
- ponytail-review on own diff: cut an unused `Decimal` import and two handlers that re-implemented the `_step()` factory (−19 lines); `media.signed_url()` deleted before it shipped (no caller until Stage 13).

### Test catalogue (owner-supplied media) — 2026-07-08
- `pices and Video/` (gitignored): 6 photos + 2 videos = **two real handsets** — Galaxy S23 Ultra (green + black) and iPhone 16 (green).
- `tests/fixtures/catalog.py` + `scripts/seed_test_catalog.py` — 8 products seeded into **Shop 01 — Dubai Marina**, covering: same model/different spec, same model/different colour, same model/different condition, same brand/different model, different brand, different category, and an **out-of-stock** row that search must never offer. Deterministic uuid5 ids → re-seeding updates in place; `--clean` removes rows + media.
- Seeding runs through the **production** code paths (`upload_media` → `create_product` → `set_boost`/`add_tags`/`toggle_featured`), so it exercises exactly what `/addproduct` does. 12 storage objects, tenant-prefixed, 2 with video.
- **No extra media needed.** `ai/orchestrator._serialize()` never sends images to the model — photo variety adds nothing to search testing. Product variety comes from rows/specs.
- Storage belongs in `specs`, not `model`. The `001_init.sql` dev seed row (`model="Galaxy S25 Ultra 512GB"`, boost 8, 4 tags, featured) bakes storage into the model name and dominates most rankings. Left in place (not ours to delete) — expect it in results.
- Verified live: all 8 ranking scenarios pass; real AI turns produce grounded replies, phrase `clearance` naturally as "special clearance deal", leak **no** `boost_level`/raw tag names, and escalate a refund instead of answering it.

### Q-015 fixed — price ordering (ADR-008 rev. 2), 2026-07-08

The test catalogue caught a live defect: *"what's your cheapest phone?"* → *"Refurbished S23 Ultra, 2,899 AED"*, while a **Galaxy S23 at 2,499 AED** sat in stock, unseen.

**The model hallucinated nothing.** It called `search_products` as instructed and truthfully reported the cheapest of the five rows handed to it. The tool returned a boost-ranked, truncated slice — and boost, whose job is to promote, had hidden a cheaper product. *Grounding a model in a tool makes it exactly as truthful as the tool.* Anti-hallucination prompting constrains the model's **memory**; it says nothing about the tool's **completeness**. And superlatives ("cheapest", "the only", "nothing under X") can never be answered from a top-N relevance slice.

Fixed at all three layers:
- `products/search.py` — `sort: relevance|price_asc|price_desc` + `max_price` filter. **Price ordering ignores `boost_level` entirely** (a promoted product must never hide a cheaper one), but still respects the customer's stated requirements, so *"cheapest Samsung"* never returns a cheaper Apple. `_SYNONYMS` maps customer words onto the schema (`phone→mobile`, `cheap→budget`, `deal→clearance`, `notebook→laptop`, …) — without it, the words customers actually use scored 0 relevance against every row.
- `llm/functions.py` — tool gains `sort` + `max_price_aed`; its **description** states that a default search cannot answer superlatives.
- `llm/prompts.py` — **superlative rule**: may not say cheapest / most expensive / "the only" / "nothing under X", or compare across the range, without running the matching sorted search.
- `ai/orchestrator.py` — validates `sort` against the enum, falls back to `relevance` (models improvise enum values; junk must not reach the query layer).

Verified live on `moonshotai/kimi-k2`: "cheapest phone" → **2,499 AED Galaxy S23**; "anything under 2600" → offers the 2,499 and nothing above; "most expensive" → **4,699 AED S23 Ultra 512GB**. No internal fields leaked. Regression-tested (`test_cheapest_ignores_boost_the_q015_regression`). **83 tests green.**

### ADR-009 — the customer never meets the machine (2026-07-08)

Owner directive: *"do not say customer like this that he is using AI. If anything happen send human, do not tell both, just inform to owner about problem and what action system takes."*

- **`FALLBACK_REPLY` deleted, not reworded.** *"Sorry, I'm having trouble right now. Please try again in a moment."* announced that a machine was answering **and** told the customer to retry into a system that was already broken.
- **Every non-answer emits the identical `ESCALATION_REPLY`.** Deliberate escalation, LLM outage, empty model response — byte-identical to the customer. A crash is indistinguishable from a routine handoff.
- **One exit:** `ai/orchestrator._handoff_to_human()`. Both the escalation path and every failure path route through it, so no future failure mode can invent its own customer-facing wording.
- **Only the owner is paged, and only for real problems.** `_alert_owner(problem, action)` fires on outages and empty responses; a refund request pages nobody. Tested both ways.
- **A failing owner alert must never cost the customer their reply** — it runs inside an `except` block and will send real Telegram at Stage 6. Wrapped in `try/except`; pinned by `test_a_failing_owner_alert_still_leaves_the_customer_answered`.
- **AI disclosure:** the prompt forbids mentioning AI/bot/automation/systems/errors. If a customer *sincerely asks* whether they're talking to a machine, the model calls `escalate_to_human` — **never denies, never confirms, never lies.** Verified live: *"are you a real person or a bot?"* → handoff. Proactive disclosure (bot-disclosure statutes) is **Q-016**, open.

Verified live on `kimi-k2.6`: simulated provider outage → customer sees only *"Let me connect you with our specialist."*, owner sees `OWNER ALERT … problem=RuntimeError: connection reset by peer action=handed the customer to a human`. **87 tests green.**

> ⚠ **Stage 6 must deliver the specialist we now promise.** Every failure path tells the customer a human is coming, but `_handoff_to_human` and `_alert_owner` currently only **log** (`ponytail:` markers at `orchestrator.py:50` and `:67`). This is the highest-priority follow-through in the repo.

### Stage 6 — Escalation / handover (SPEC §3; ADR-009 debt paid)
- `escalations/context.py` — **Redis conversation memory.** Every turn recorded as it happens (customer / assistant / shopkeeper), last 25 kept (SPEC §7 will snapshot these), 24h TTL. A poisoned entry is dropped, not fatal.
- `escalations/service.py` — `escalate()` (row + freeze + notify), `is_frozen()`, `reply()`, `handover()`, `forward_to_shopkeepers()`, `alert_owner()`. Typed errors `NoPendingEscalation`, `DeliveryFailed`.
- `telegram_bot/notify.py` — outbound Telegram outside any handler: `send_to_owner`, `send_to_shopkeepers`, `send_to_customer`. Lives in `telegram_bot/` (which owns Telegram) so `escalations → telegram_bot.bot → escalations` never cycles. **Every send is best-effort and never raises.**
- `messaging/pipeline.py` — **step 4b: escalation freeze.** Freeze and `bypass_ai` do the same thing (no AI, forward to staff), so both route through one `_to_humans()`, which also `remember()`s the message. This retired the Stage-7 bypass-forward marker.
- `ai/orchestrator.py` — **the AI is multi-turn.** `_replay()` loads the session each message; a shopkeeper's turns replay as *assistant* turns, so the AI resumes a conversation a human was holding without being told a human held it. `_handoff_to_human` now really escalates; `alert_owner` really pages the owner.
- `telegram_bot/bot.py` — `/reply <customer> <text>` and `/handover <customer>` live on the keeper bot. The `product_command` decorator was renamed `keeper_command` (my change made its old name a lie) and taught the new typed errors.
- **"AI resumes with full Redis context" needed no restore step.** Because every turn was recorded all along, `/handover` is just an unfreeze. That is the whole feature.
- **Freeze happens before notify** — if notification is slow, the customer's next message must already miss the AI (`test_escalate_freezes_before_notifying`).
- **Tenant guard:** the customer id in `/reply`/`/handover` is shopkeeper free text. Freeze is keyed per shop and both commands refuse a customer not frozen *for this shop*, so Shop B cannot answer or hijack Shop A's escalated customer. Two tests pin it.
- **Nobody heard? The owner is paged.** If `send_to_shopkeepers` reaches zero staff, `escalate()` alerts the owner: the customer has been promised a specialist and somebody must know nobody heard.
- Tests: `tests/escalations/test_service.py` (10), `tests/escalations/test_context.py` (7), pipeline +4 (frozen, remembered, per-shop scoping, failing forward), orchestrator rewritten (+3 multi-turn). **111 total green, still zero network.**
- Live-verified on the real project: owner alert delivered over Telegram; `escalate → pending_escalations row → AI frozen → /handover → unfrozen + resolved_at set`; row cleaned up after. (`resolved_at: "now()"` **does** work through PostgREST — checked rather than assumed.)
- ponytail-review on own diff: cut `reply()`'s unused `client` param.

### Stage 7 — Security (SPEC §7, §8)
- `security/detectors.py` — **6 attack patterns as pure functions** (`detect_attack(text, *, msg_count_60s) → AttackResult | None`): prompt injection (phrases + base64 blob + >2000 chars), SQL injection, rapid-fire (≥20/60s, volume not content — the count is passed in so the function stays pure), cross-shop probing, admin commands in customer text, credential probing. Content checked first, rapid-fire last. A curated **clean set** of real shopping questions is tested to stay clean — a false positive quarantines a paying customer.
- `security/service.py` — the stateful side, mirroring `escalations/service.py`. Owns the Redis keys the pipeline reads: `quarantine:{id}` (1h TTL), `bypass_ai:{id}` / `blacklist:{id}` (no TTL), `rate:{id}` (60s window). `quarantine()` locks the number, snapshots the **last 25** via `escalations.context.history()` into `security_incidents`, and pages the owner with the incident id + one-tap follow-up commands. **A failed incident DB write still holds the block and still alerts** (forensics best-effort, block is not). `blacklist()` writes Redis + a durable `blacklisted_phones` row and supersedes any quarantine. `forward_to_shop()` = lift quarantine + set bypass (false-positive recovery).
- **Pipeline wired (`messaging/pipeline.py`):** step 3 blacklist (silent ignore), step 4 quarantine (generic reply), step 6 attack detection (auto-quarantine + owner alert). Steps 4/5 refactored from inline `redis.exists(...)` to `security.is_quarantined/is_bypassed` — **the quarantine/bypass/blacklist key strings now have exactly one owner** (`security/service.py`), so writer and reader can't drift.
- **Owner commands** on the control bot (owner-only): `/investigate <id>` `/quarantine_extend` `/quarantine_lift` `/blacklist` `/forward_to_shop` `/bypass_ai` `/bypass_remove`.
- **The generic quarantine reply is intentionally shown to the (attacker) customer** — "Your message could not be processed." SPEC §7 mandates it. This is NOT an ADR-009 violation: ADR-009 protects *genuine* customers from meeting the machine; a detected attacker gets a deliberately flat, revealing-nothing reply. A false positive is recovered by the owner via `/quarantine_lift` or `/forward_to_shop` (which is why the owner alert carries those commands).
- Tests: `tests/security/test_detectors.py` (patterns + clean set + forensic trigger), `tests/security/test_service.py` (quarantine/incident/alert, DB-failure resilience, blacklist, bypass, forward, rate), pipeline +3 (attack→quarantine→next-msg-quarantined, blacklist silent, rapid-fire), bot +1 (owner security commands registered). **143 total green, still zero network.**
- ponytail-review on own diff: built as **2 files not 3** (folded `quarantine.py`+`bypass.py` into one `service.py`); deleted the dead `_is_blacklisted` pipeline stub; routed both pipeline readers through `security` instead of duplicating key strings. 3 new markers (blacklist Redis-vs-DB, quarantine-extend fixed TTL, base64 heuristic).

### Stage 8 — Profit + reporting (SPEC §6)
- `orders/models.py` — **pure money path**: `line_profit(sell, disc, cost, qty) = (sell - disc) - cost×qty` and `ProfitSummary` (orders/revenue/discounts/cost/profit/clearance/top; `.margin` guards divide-by-zero). **cost is per-unit × quantity** — SPEC's literal formula omits the quantity, which would silently under-count cost on multi-unit orders. Self-checked in `__main__` + tests.
- `orders/service.py` — `create_order()` is the **only writer** of `orders`; it reuses `products.service.get_product` as the tenant guard (product must belong to the shop) and writes the first `order_status_history` row. `profit_summary(shop_id, start, end)` fetches the range embedding `products(cost_price,brand,model,tags)` and aggregates in Python (`ponytail:` — RPC at scale). Cancelled orders excluded.
- `reports/service.py` — `parse_period` (`today|yesterday|weekly|monthly|YYYY-MM-DD` → UTC `[start,end)`), `format_profit`, `format_owner_profit` (monospace, emojis, AED).
- **Commands:** keeper `/profit [period]`; owner `/owner profit [all|compare|shop <id>] [period]` (new `/owner` dispatcher on the control bot — Stage 10 hangs dashboards off it). `all` and `compare` render one per-shop + total view.
- **Live-verified:** created a real order (S25 Ultra ×2, 100 discount) → `profit_summary` → `Revenue 6,800 · Cost 5,600 · Profit 1,100 · Margin 19.6%` → deleted. Quantity handled, cleanup clean.
- Tests: `tests/orders/test_service.py` (aggregation totals/clearance/top-grouping/empty, create_order tenant-guard + status history, range query), `tests/reports/test_service.py` (period windows + junk rejection, profit/owner formatting). **153 total green.**
- **Deferred (challenged, not silently dropped):** `/report daily|inventory_low|top_products`, `/productstats` (Q-014), client-grouped owner reports + `/owner usage` → Stage 10.

### Stage 8b — Hybrid order booking (Q-017 → **ADR-010**)
- **The AI drafts, a human confirms.** Assistant collects the order and calls the new `place_order` tool → a `draft` order + a Telegram notice to the shop's staff. Shopkeeper `/orders` (list), `/confirmorder <#>`, `/rejectorder <#> [reason]`.
- `orders/service.py` — `draft_order` (stock check + bargaining-floor guard + supersede prior draft + notify shop), `confirm_order` (**atomic** `decrement_stock` RPC + status→confirmed + tell customer the order number), `reject_order` (cancel, no cold customer message), `list_drafts`.
- `migrations/003_order_drafts.sql` (**applied live**) — `orders.status` += `draft`; `orders.order_number` (human serial); `products.min_price` (bargaining floor); `decrement_stock()` RPC (one conditional UPDATE — two racing confirms can't oversell).
- **Inventory:** checked at draft (never bother the shop with an out-of-stock order) AND atomically at confirm.
- **No premature message (design #2):** the customer hears nothing about an order until the shop confirms; then they get the order number + summary. Reject → the AI keeps serving them; staff `/reply` if they want to explain.
- **Bargaining — human-in-the-loop (ADR-010 Revision 1; owner directive "no rubber stamp, in the loop"):** the AI has **no** discount authority. Customer haggles → AI calls `request_price(product, offer)` (never quotes a discount) → `price_requests` row + shop notice (offer / list / **cost** / margin). Shopkeeper `/approveprice <#>`, `/custom <#> <price>` (counter), `/denyprice <#>`; the **system** tells the customer. `draft_order` applies only a shop-**approved** price. Per-shop toggle `/negotiation on|off` — **off** is checked fresh from the DB every haggle, so a shop that just turned it off gives no discount even before its bot restarts. The old secret `min_price` floor is **removed** (migration 004 drops the column).
- **Concurrency (design #3):** a pending draft does NOT freeze the AI; a new order intent supersedes the old draft. True per-message ordering is the Stage 11 session lock.
- **Live-verified:** draft #1 → `/confirmorder` → `confirmed`, stock `5→4` (atomic), restored. Negotiation: `request_price` → `/custom 3200` → draft discount `200` (list 3400); `/negotiation off` → request refused. Out-of-stock confirm raises (nothing oversold) — unit-pinned.
- Tests: `tests/orders/test_service.py` (draft in-stock/out-of-stock/**approved-price**, confirm decrement+notify, confirm-out-of-stock-raises, reject-no-cold-message, **request/approve/custom/deny/negotiation-off**). **164 total green.**

### Stage 9 — Excel order export (SPEC §10)
- `utils/excel.py` — **pure openpyxl builder** `orders_workbook(rows, *, detailed)`. Takes the DB order shape (embedded `products`, and `delivery_persons` for rider sheets) and flattens it: SPEC §10 columns (Order ID … Status), white-bold-on-`#2563EB` header, thin borders, auto-width (capped 50), frozen header row. `detailed` adds Order Time / Rider Name / Rider Phone / Special Instructions. **RAM/Storage extracted from `products.specs`** (case-insensitive; `storage` or `rom`). `Selling Price` = `selling_price − discount_amount` (what the customer actually pays). No I/O → unit-testable by reloading the bytes.
- `utils/storage.py` — `upload_report(shop_id, filename, data)` → uploads to the private `shop-reports` bucket under `{shop_id}/…` and returns a **24h signed URL** (SPEC §10). Separate from `products/media.py` (product-scoped, never signs). Handles both `signedURL`/`signedUrl` key spellings and a path-vs-absolute URL.
- `orders/service.py` — `orders_for_export(shop_id, filter)` (`today|yesterday|YYYY-MM-DD|pending|all`; **drafts never exported**; `pending` = `confirmed`; reuses `reports.parse_period` for the date filters), `rider_orders_for_export(shop_id, rider_id, filter)` (one rider, **sorted by address**), and the thin orchestrators `export_orders(...)` / `export_rider(...)` → `(filename, signed_url, row_count)`.
- **Commands (keeper bot):** `/exportorders [today|yesterday|YYYY-MM-DD|pending|all] [detailed]` and `/exportrider <rider_id> [period]`. `/exportorders detailed` alone = today+detailed. Bad period/uuid → `ValueError` → safe reply via `keeper_command`.
- `migrations/005_reports_bucket.sql` (**applied live**) — creates the private `shop-reports` bucket (deferred from `002` until something wrote here). `config/settings.py` += `supabase_reports_bucket`.
- **Live-verified** against the real project: `orders_for_export` (live query) → `orders_workbook` → `upload_report` → **downloaded the signed URL** → valid `.xlsx` (header `Order ID`, 5,325 bytes) → object deleted. Full round-trip through real Supabase Storage.
- Tests: `tests/utils/test_excel.py` (4: header + `#2563EB` style, row mapping + net price, detailed rider columns, base sheet has no detail columns) + `excel.py` `__main__` self-check. **168 total green.** Storage upload is network → live-verified, not in the suite.
- **Empty-but-real (ponytail):** `/exportrider` works today but nothing assigns `rider_id` to orders yet (the draft/confirm flow leaves it null), so rider sheets are empty until rider-assignment lands (Q-006). Export ships testable-but-empty, exactly like profit did before booking. *(As of Stage 12b: rider assignment is built — `/exportrider` now has real data. This note is historical, describing Stage 9 at the time.)*

### Stage 10 (in progress) — beat usage-flush (SPEC §12/§13; ADR-006)
- `tasks/celery_app.py` — **`celery_app.conf.beat_schedule` is born.** `flush-usage-counters` runs `flush_usage_counters` hourly (`crontab(minute=15)`, `conf.timezone="UTC"`). `celery_beat` was booting and scheduling nothing; now it has a job.
- `tasks/tasks.py` — `flush_usage(repo, redis, *, today=None)` (testable core, deps injected like `_run`) + the `flush_usage_counters` task wrapper. Drains every `usage:*` key for a **completed** day (`day < today` UTC) via `getdel` and `repo.upsert_usage`, then the key is gone. **Today's still-incrementing key is skipped** — `upsert_usage` overwrites the row's count, so draining mid-day would lose every later message. Idempotent (a re-run finds nothing), malformed keys skipped.
- `messaging/pipeline.py` — the usage-key **writer** now also owns the reader: `USAGE_KEY_PREFIX` + `parse_usage_key(key) -> (client_id, shop_id, day, metric) | None` live next to `_incr_usage`, so format and parse can't drift (same principle as the security keys).
- **Hourly, not once-at-midnight (ponytail):** the flush only touches completed days, so extra runs are no-ops — but a single missed midnight tick would otherwise strand a full day of billing until the next. Hourly self-heals within the hour. Ceiling: a **>2-day** beat outage can still lose a day (the counter's 2-day TTL) — an ops alarm, not a code fix.
- **Usage-flush live-verified:** seeded a real Redis (Memurai) completed-day counter `=7` → `flush_usage` → key `getdel`'d → `usage_daily` row `count=7` written live → row deleted.
- **Health check + `/health` + dashboards.** One checker `reports/health.check_health(redis, repo) -> HealthReport` (DB via new cheap `repo.health_check()`; Redis ping; LLM `is_configured`; Twilio = mocked; Celery = `control.ping()` worker count; metrics = `session:*` + `quarantine:*` counts). **Two callers:** the `GET /health` endpoint (`main.py`, 503 when unhealthy) and the `health_check` beat task (every 60s → `send_to_owner` on failure — not `alert_owner`, whose signature is shop/customer-scoped). `ok` gates only on DB+Redis+Celery (LLM/Twilio are config-state; Twilio is mocked by design). Owner dashboards on the `/owner` dispatcher: **`dashboard`** (shops active/paused, today profit all, open escalations, health line), **`health`** (full checker), **`escalations`** (open list — new `escalations.list_open`/`count_open`), **`security`** (recent incidents — new `security.recent_incidents`), **`audit`** (honest "empty until Stage 12 — nothing writes `audit_logs`").
- **Health live-verified on real infra:** `check_health` against live Supabase + Memurai reported db/redis/llm/twilio ✅ and **celery `down: no workers`** (honest, none running); started a real `celery -P solo` worker on Memurai → re-ran → **celery `ok (1 worker)`, `ok=True`** → stopped the worker. `control.ping()` truly detects workers.
- Tests: `tests/tasks/test_tasks.py` +4 (flush) · `tests/reports/test_health.py` +5 (healthy/all-up + metric counts, no-workers unhealthy, db-down unhealthy, format both states, beat pages owner only when unhealthy). **177 total green.**

### Stage 11 — concurrency / reliability hardening (SPEC §11)
- **Per-session Redis lock** — `process_message` now acquires `lock:session:{shop_id}:{identity}` (`SET NX EX 30`) around the whole SPEC §9 body (moved into `_dispatch`), releases in `finally`. A contended message returns `PipelineResult(None, "locked")`.
- **MessageSid dedup** — a re-delivered Twilio message (same `message_sid`) is dropped: `dedup:{sid}` `SET NX EX 300` (5-min). Telegram carries no sid → never deduped.
- **Order is lock-first, dedup-second (deliberate):** a lock miss returns *before* the sid is marked seen, so the Celery `self.retry` the Twilio path will do at Stage 13 re-runs cleanly instead of being deduped away. `lock_key`/`_is_duplicate` own the key formats.
- **Live Telegram path is unaffected:** the bots run without `concurrent_updates`, so PTB processes one update at a time per bot — `locked` cannot fire there today. It exists for the Stage 13 Celery/Twilio path (where the `self.retry`-on-`locked` wiring lands — `ponytail:` marker).
- **Celery reliability** — `task_acks_late=True` + `worker_prefetch_multiplier=1`: a task a crashed worker was running is redelivered, not lost (safe — `flush_usage` is idempotent, `health_check` stateless).
- **Already done earlier:** "OpenAI errors: retry once then fallback" (llm_client retry-once + the single `ESCALATION_REPLY` via ADR-009) and "webhook returns 200 immediately" (Stage 3).
- **Live-verified** on Memurai: held lock → `locked`; normal → `ai` + lock freed; same MessageSid twice → `ai`/`duplicate` (dedup TTL 300). Cleaned up.
- Tests: `tests/messaging/test_pipeline.py` +4 (lock released after processing, held lock defers, duplicate sid dropped, no-sid never deduped). **181 total green.**
- **Deferred (honest):** the 300+-concurrent load test — it needs the live Twilio producer (dormant till Stage 13) + real infra; a fakeredis micro-bench proves nothing. Run it when the producer exists.

### Stage 12 — production hardening (SPEC §16)
- **Audit trail (`audit/service.py`).** `record(actor, action, *, shop_id, detail)` appends to `audit_logs`; `recent(limit, shop_id)` reads. **Wired at the two command wrappers** (`owner_only`, `keeper_command`, in the `else`-on-success branch) — they gate exactly the privileged owner/keeper commands, so every state-changing action is logged with its actor in **one place**, no per-command edits (root-cause placement). `/owner audit` now reads real rows (was honest-empty). **`record` never raises** — a failed audit (even a broken client) is swallowed, so it can't break the action it records.
- **Structured logging (`core/logging.py`).** `setup_logging()` — `time level logger message`, level from settings, `force=True` (idempotent). Called at both process entrypoints: `run_all_polling` (bots) and `main.py` (API). Replaced the ad-hoc `basicConfig`. JSON formatter deferred (`ponytail:` — no aggregator to consume it yet).
- **Live-verified:** wrote a real `audit_logs` row → `recent` read it back → deleted; `setup_logging` emits the structured line format. Suite stays offline via an autouse stub of `audit.record` in the bot tests.
- Tests: `tests/audit/test_service.py` +4 (record writes actor/action/detail, defaults shop=None/detail={}, **swallows a DB failure**, recent reads back). **185 total green.**
- **Deferred (deploy-time / blocked — challenged, not dropped):**
  - **CI** — repo is not a git repo yet; a GitHub Actions YAML would run nowhere. Add `.github/workflows/ci.yml` (just `pytest -q` with the `PYTHONPATH="src;config;."` env) when the repo is initialized.
  - **`AI_PROVIDER=openai` (GPT-4o) flip** — a **deploy-time env change**, not code. The abstraction already supports it (`ai_provider` Literal includes `openai`). Flipping now would break the live bots (no OpenAI key present, Moonshot is what's wired). Do it at prod cutover + live re-verify.
  - **Prod Docker** — `Dockerfile` + `docker-compose.yml` already exist (Stage 0) and are prod-usable; add an env-specific compose override at deploy.
  - **Error-handling sweep** — the ADR-009 posture already holds (notify/pipeline/orchestrator/audit never raise; DB-write failures still hold security blocks). No specific defect to fix — a blind sweep isn't a lazy-good task; revisit if a real gap surfaces.

### Stage 12b — full live QA audit + delivery/rider/COD build-out (SPEC §6, §10), 2026-07-11/12

**8-goal live audit** (customer chat · escalation/security · inventory · order/delivery · owner
oversight · concurrency/edge · reports/accuracy · UX/errors), driven end-to-end against the live
Telegram bots + live Supabase, not mocks. Found and fixed:

- **[Found+fixed] Over-length false-quarantine.** A clean message >2000 chars (no injection content)
  tripped `detect_attack`'s length rule and quarantined a paying customer. Root cause was one rule in
  `security/detectors.py::detect_attack` (`len(text) > MAX_MESSAGE_CHARS` → `attack_type="injection"`).
  Length alone no longer quarantines; injection phrases/base64/SQL are still caught *inside* a long
  message. `messaging/pipeline.py` now handles the over-length case on its own terms — a friendly
  "shorten it to a sentence or two" reply (`action="too_long"`), not a security block.
- **[Built] Fulfilment status** — `orders.advance_delivery` + keeper `/deliveryupdate <#>
  packed|shipped|delivered`, one step at a time (`_is_next_step`, pure, no skipping/backward/off-flow),
  customer told at each step.
- **[Decided+built] Timezone — UAE-only, no per-shop column.** Owner confirmed the business is UAE-only,
  so `reports.service` uses a single `DUBAI = timezone(+4)` day boundary (`parse_period`/`_day`), not
  UTC. A per-shop `timezone` column was considered and explicitly rejected — not needed for a
  single-timezone business, would have been unused configurability.
- **[Decided+built] Rider onboarding + assignment + COD (Q-006 resolved).** Owner decisions: owner
  onboards riders (shop can have >1); one global rider bot (`@Rider001_bot`, token delivered via a
  root file, wired into `.env`, file deleted); rider links Telegram by sharing their contact (phone
  matched, `riders/service.py::_normalize_phone`, UAE 9-digit form). Built:
  - `riders/service.py` (new module) + `migrations/007_rider_telegram.sql` (`delivery_persons.telegram_id`)
    and `migrations/008_cod_custody.sql` (`orders` gains `cod_amount`/`cash_received`/`delivered_at`/
    `custody`/`custody_at`/`cancel_remarks`; new `cod_ledger` table) — **both applied live**.
  - Owner: `/addrider <shop> <phone> <name>`, `/riders <shop>`.
  - Keeper: `/riders`, `/assigndelivery <order#> <rider_id>` (card shows COD + rider's running
    balance), `/reconcilecod <rider|name> <amount>` (owner's exact formula: *previous balance + today
    COD − handed over = remaining*; same trail pushed to the rider).
  - Rider bot (`build_rider_application`, new global bot alongside owner/keeper/customer):
    `/mydeliveries`, `/accept <#>` / `/notreceived <#>` (**custody handshake** — the audit mechanism
    the owner asked for: rider can't claim "never got it," shop can't claim "we gave it"; the answer
    is written once, `/deliver` is blocked until `/accept`), `/deliver <#>` (registers time, then asks
    cash received, finalizes on the reply — status delivered, `cod_ledger` 'collect' row, customer +
    shop notified), `/canceldelivery <#> <remarks>` (remarks mandatory, stock restored), `/myreport
    [period | from to]`.
  - Money is an **append-only ledger** (`collect`/`handover` rows), balance always `Σcollect −
    Σhandover` — never a mutable counter that can drift.
- **Live-verified** (not just unit-tested): full lifecycle on Shop 01 order #7 — assign (COD card
  pushed) → `/accept` (re-deciding blocked) → `/deliver` (cash 1500) → `/myreport` shows it →
  `/reconcilecod` (1650 handed over → 50 AED remaining) — every DB row checked byte-for-byte against
  the formula. Owner's Telegram received the rider assignment card, the delivery confirmations, and a
  summary — this is genuinely running on the live bots, not a demo.
- Tests: `tests/riders/test_service.py` (new, 26: phone normalize, custody transitions incl.
  write-once, deliverable matrix, cash parsing, report windows, COD trail math, deliver/cancel/
  reconcile flows), `tests/orders/test_service.py` +COD-aware assignment tests, `tests/reports/
  test_service.py` +Dubai-offset assertion, `tests/telegram_bot/test_bot.py` +rider-bot-in-runner.
  **235 total green** (was 185 at Stage 12 close, +50 across the audit + delivery/rider/COD work).
- **Low-severity, reported not fixed** (revisit if it matters): `delivery_date` isn't captured to a
  structured column separately from free text; customer phone is absorbed into the address field on
  some paths; the attack-forensics snapshot doesn't include the triggering message itself; no
  DB-level `selling_price >= cost_price` guard (relies on app-layer discount bounds in
  `approve_price`).

### Stage 12c — inline-button UX (all bots) + security audit suite + shop-owner bot (7th bot), 2026-07-13/14

- **Inline buttons on every bot.** `telegram_bot/keyboards.py` (new) — `cb()`/`parse_cb()` build/parse
  `action:arg1:arg2` callback data (`≤64` bytes, asserted at build time), one prefix per bot (`o`=owner,
  `k`=keeper, `r`=rider, `s`=shop-owner). Every menu button calls the **same service function** the
  matching slash command calls — a button is a second entry point, never a second implementation.
  `telegram_bot/format.py` (new) — `to_telegram_html`/`escape_html`, used at the `/addproduct`
  confirmation (shopkeeper-typed brand/model could contain Markdown special chars → Telegram 400;
  now Telegram HTML with the dynamic parts escaped, can't 400). `whatsapp/format.py` (new, Stage 13
  prep) — Markdown → WhatsApp-native scrubber, self-checked, not wired into a channel yet.
- **`tests/audit_suite/` (new)** — a security/correctness audit distinct from the unit suite: Category 1
  tenant isolation vs prompt injection (model-supplied `shop_id` never reaches a query — the *active*
  shop is passed positionally), Category 2 webhook MessageSid dedup under concurrency, Category 3
  atomic-stock-decrement race (two confirmations, stock=1 → exactly one wins, never negative), Category 4
  WhatsApp text sanitization, Stage-1 Telegram HTML-safety, Stage-2 callback tenant isolation (a foreign
  shop/rider id in callback data fails closed), Stage-3 checkout race, `test_stage_bot_commands.py`
  (every registered command on every bot actually runs without crashing — the harness that catches "a
  new bot was wired but a command was forgotten").
- **Two bugs found and fixed while building the button layer:** `/addproduct` confirmation 400'd on a
  brand/model containing Markdown special characters (fixed above); rider bot `/start` crashed with
  `UnboundLocalError` — a local variable named `kb` shadowed the `keyboards` module alias.
- **Shop-owner bot (7th bot, ADR-006) — remote oversight for a client owning 1+ shops.** Owner directive:
  *"if owner even not come on shop he knows everything from this bot and no one can make him fool or do
  corruption in shops"* — but *"security incident and escalation… is subject to me [the platform owner],
  it just focus on his business… if he see these type of things he get scared and stop using system."*
  Built accordingly: **deliberately no security/escalation views** on this bot; full financial/operational
  visibility instead.
  - `migrations/009_shopowner_messages.sql` (**applied live**) — `clients.telegram_id` (nullable, NOT
    unique — matched by normalized phone like `delivery_persons.telegram_id`, migration 007 precedent);
    new `messages` table (shop_id, identity, role `customer|assistant|shopkeeper`, content, created_at) —
    a **permanent chat archive**, dual-written alongside the existing Redis session
    (`escalations/context.py::remember()` gained a best-effort second write via
    `messaging/store.py::save_message`). Redis stays the AI's 25-message/24h working memory; `messages`
    is the durable, shop-owner-facing record. Deletion is **platform-owner-only** (the owner bot's new
    🧹 Messages menu: delete all / one shop / a date range, typed-`YES` confirmed).
  - `messaging/store.py` (new) — `save_message` (never raises), `conversations` (distinct recent
    identities), `transcript` (last 25 turns), `delete_messages(shop_id=None, start=None, end=None)`
    (always has a WHERE clause — no bare `DELETE FROM messages`), pure formatters.
  - `tenants/service.py` — `client_by_telegram`/`link_client_telegram` passthroughs; both repos
    implement `get_client_by_telegram_id`/`link_client_telegram` (phone-matched, first-match-wins).
  - Bot: `_own_shop(service, client, shop_id)` is **the** tenant guard — resolves a shop and rejects with
    the identical "not found" whether the id is unknown or belongs to another client, called on every
    shop-id-carrying button. Menu: 🏪 My shops → profit / orders (today/yesterday/pending/all) /
    inventory / riders & COD balance / Excel export / message transcripts; 📊 Analytics → compare shops /
    top products / cancels+discounts / COD outstanding across every owned shop.
  - Owner-provided bot token was delivered as a root `api.txt` file, wired into gitignored `.env`, file
    deleted (same consume-and-delete pattern as every other credential in this project).
  - `scripts/run_bots_live.sh` (new) — pins `.venv/Scripts/python.exe` explicitly (bare `python` on PATH
    lacks project deps) so the live 7-bot launch actually starts here; `.claude/settings.local.json`
    (gitignored) allow-lists running it.
- **Live-verified:** real Client A linked to a real Telegram id via contact-share, `client_by_telegram`
  resolved to the correct 2 owned shops; all 7 bots confirmed polling with real inbound traffic.
- Tests: `tests/audit_suite/test_shopowner_isolation.py` (new, cross-client isolation),
  `tests/messaging/test_store.py` (new), `tests/audit_suite/test_stage_bot_commands.py` extended to a
  7-bot topology + shop-owner command coverage, `tests/telegram_bot/test_buttons.py` (new, keyboard
  builders + dispatch routing for every button on every bot). **306 total green** after 12c
  (`tests/audit_suite/` alone: 52 tests, zero warnings).

### Stage 12d — gap-fix wave: friendly IDs, keeper/owner/shop-owner features, counter sales, 2026-07-16

A structured 14-item gap report from live use of the 7-bot system, worked as a 6-phase plan (planned,
verified against code by 2 Explore agents + a Plan agent before a line was written — see
`Plan.txt`/the archived plan `there-are-some-gaps-floofy-falcon.md`). One item was a real bug; the rest
were missing features, each resolved with an explicit owner decision where the plan branched.

- **`migrations/010_friendly_ids_counter_sales.sql` (applied live)** — `products.product_number` /
  `delivery_persons.rider_number` (`bigint generated by default as identity` + unique index each,
  same auto-backfill mechanism as `orders.order_number`/migration 003 — **verified live: 10 products
  → PR0001–PR0010, 1 rider → rider001, zero nulls**); `products.min_qty` (low-stock threshold, default
  0 = alerts off — no behaviour change until a keeper sets one); new `counter_sales` table (per-unit
  price, Dubai `sold_on` date, photo proof path, `recorded_by`, durable `discrepancy` flag).
- **Phase 1 — bug fix + rider report.** `list_price_requests` selected a column named `identity` that
  never existed (live column is `phone`) → every keeper `/pricerequests` press returned "Internal
  error." Fixed at both the query and the renderer. `my_deliveries` had no date filter and a flat
  15-row cap (an old undelivered order could silently vanish off the bottom); rewritten as in-flight
  (any age — it's still the rider's job) + delivered-today, two queries merged in Python
  (`ponytail:` — swap to one PostgREST `.or_()` if round-trips ever matter). `/myreport` was a flat
  `#num — cash — time` list; now grouped under a `🗓 date` header per Dubai day with item/qty/address.
- **Phase 2 — friendly reference codes.** `utils/codes.py` (new, pure) — `product_code`/`rider_code`
  render `PR0001`/`rider001`; `parse_product_code`/`parse_rider_code` accept `PR0001`, `pr1`, or a
  plain int and return `None` (not an exception) on junk, so the caller folds it into the same
  "not found" a wrong UUID gets. `products.service.get_product_by_ref` and `bot._resolve_product` /
  `bot._resolve_rider` are now **the** choke points every keeper-facing command routes through
  (boost/tag/untag/cleartags/feature, `/assigndelivery`, `/exportrider`, the button-prompt edits) —
  one place, not one guard per caller. Callback data still carries UUIDs (a code would waste the
  64-byte budget); displays (rider list, `/addrider` replies, `/addproduct` confirmation, a new 🆔
  ID list button) show codes.
- **Phase 3 — keeper features.** Real `/productstats [period]` (was Q-014's honest stub) — folds
  orders (same exclusions as `profit_summary`, so the two reports can never disagree) against the
  full catalogue; unsold products list with zeros, because dead stock is the point of the report.
  `notify.send_to_shopowner` (mirrors `send_to_rider`) + `orders.notify_low_stock` — fires only after
  a successful **positive** stock decrement (never on a restock), pings the shop **and** the client
  owner if linked, best-effort. `/addproduct` grew a 12th step (MINQTY, after QUANTITY) and a second
  entry point — a ➕ Add product button now starts the same `ConversationHandler` a slash command does.
  `utils/excel.py` gained a generic `sheet_workbook(title, headers, rows)`; `orders_workbook` is now a
  thin wrapper over it (signature unchanged — every existing caller and test kept working). New 🧾
  Counter sheet button/`​/countersheet` — an Excel of the catalogue with empty "Price sold"/"Qty sold"
  columns for the shop to fill by hand.
- **Phase 4 — platform-owner onboarding + analytics + escalation resolve.** `create_client`/
  `create_shop`/`create_shopkeeper` existed only as **repo methods with zero callers** — no bot command,
  no UI, reachable only from a hardcoded seed script. Now a real ➕ Onboarding menu (add client → shop →
  bot tokens → shopkeeper, semicolon-separated typed prompts) plus `/addclient` `/addshop`
  `/setshoptokens` `/addkeeper`; `update_shop_tokens` added to `TenantRepo` (both repos), `None` per
  field = leave it unchanged. Token-set replies are honest that a shop's bots only start polling on the
  **next process restart** (applications are built once at boot; hot-reload was explicitly not built —
  YAGNI for a single-operator system). Owner analytics: 🏆 Top products / 🕵️ Cancels+discounts / 💵 COD
  outstanding across every shop — the same formatters the shop-owner bot already used, just called over
  `service.list_shops()` instead of one client's shops (the period-menu builder was already
  prefix-agnostic, no new keyboard code needed). `escalations.resolve_escalation` is now **public**
  (`/reply` answered a customer but never closed the row — escalations piled up forever); the owner's
  escalation list now renders one message per row with a ✔️ Resolve button, and `/handover` routes
  through the same function instead of duplicating it.
- **Phase 5 — shop-owner date range + activity logs.** `orders.orders_in_range` (range twin of
  `orders_for_export`, ordered by `created_at` so the view can group by day) + a 🗓 Date range button —
  the shop-owner bot's **first free-text consumer** (`_shopowner_text`; fails silent with no pending
  prompt, and **re-guards the shop id when the text lands** — a pending prompt is not a capability).
  Anti-corruption logging: only slash commands were audited before this phase, so a keeper working
  entirely through buttons left **no trail at all**. `_audit` gained a `detail`/`shop_id` override;
  the mutating inline buttons (confirm/reject/deliver-step/approve/counter/deny/assign/reconcile/
  negotiation/counter-sheet) are now audited on success, same rule `keeper_command` already used for
  slash commands; read-only button presses stay unaudited (they'd bury the real actions in noise).
  Rider actions carry the shop of the order they touched, because the rider bot is global (no shop in
  `bot_data`) — without that, the rows would land `shop_id=NULL` and never reach that shop's owner.
  `format_activity` + a `_HUMAN_ACTIONS` map render rows as sentences ("14:22 · Ali — confirmed order
  #7"); slash and button map to identical phrasing; an unmapped action still shows (raw name + text
  snippet) rather than vanishing. New 📋 Logs button — the first caller of `audit.recent`'s `shop_id`
  filter (it existed with zero callers before this phase).
- **Phase 6 — counter (walk-in) sales.** The system was blind to counter sales entirely, so `/profit`
  only ever described half the business. Owner-decided flow (2 of 2 proposed options): the shop prints
  the counter sheet and fills it by hand; the shop owner photographs the filled sheet and sends it to
  their bot; a vision model reads it into rows; the **owner sees the parsed rows and confirms before
  anything is written** (man-in-the-middle by design — the AI never writes stock unsupervised).
  - `llm/llm_client.py` — `LLMMessage.content` widened `str → str | list[dict]` for OpenAI content-parts
    (vision); `_to_wire` already passed content through untouched, so the wire format needed no change.
    `chat()` gained a `model` override so only this one flow leaves the configured chat model.
  - `orders/counter_sales.py` (new) — `EXTRACT_PROMPT` (strict JSON, no invented codes), pure
    `parse_extraction` (strips markdown fences models add despite being told not to; drops any row
    without a readable code+qty; never raises on junk, raises a human-actionable message only when the
    *whole* sheet is unreadable), `extract_rows` (photo bytes → base64 data URL → vision-model call),
    `record_sales` (per row: resolve by friendly code (tenant-guarded) → atomic stock decrement → if
    the stock can't cover it, **the row is still inserted, flagged `discrepancy=true`, stock left
    untouched** — the sheet says sold, the system says impossible, and that contradiction is the single
    most useful thing the table stores, so it is never dropped, auto-corrected, or counted as revenue).
  - Shop-owner bot: 🧾 Today sell → shop picker → send a photo → preview with product names resolved →
    ✅ Save all / ❌ Discard. A typed correction (`PR0001 2 3400`, one line per sale) **replaces** the
    model's reading wholesale rather than merging with it — the human's word is final. Rows stash in
    `chat_data`, not callback data (far past the 64-byte cap); `# ponytail:` marker — a process restart
    loses an in-progress stash, the owner resends the photo (upgrade to Redis if that ever bites). The
    photo itself is kept in `shop-reports` as the evidence behind every row (`upload_report` gained a
    `content_type` param, default xlsx).
  - `orders/models.py` — `ProfitSummary` gained `counter_revenue`/`counter_profit` (trailing defaults,
    every existing positional constructor stays valid). Pure `merge_counter(summary, counter_rows)`
    folds counter revenue/cost/profit in and merges top products **across both channels** — the shop
    owner wants the best seller, not the best seller *online*. `orders.profit_summary` keeps its exact
    name and signature (bot tests monkeypatch it by name) and now folds counter sales in automatically;
    `format_profit` shows a counter-sales line only when non-zero, so a shop with no counter sales reads
    an unchanged report. New 🧾 Counter sales report (date-grouped, discrepancies called out) on the
    shop-owner analytics menu.
- **Live-verified end to end, not just unit-tested:** migration applied to the real project (backfill
  sanity-checked); `PR0001`/`pr1` both resolve on the live DB and a cross-shop `PR0001` correctly
  returns "not found"; a live `/productstats` total **exactly matched** the live `/profit` total for
  the same shop/period (the whole point of sharing `profit_summary`'s exclusions); the counter sheet
  exported 10 real products to Storage with a working signed URL; the vision model id
  (`moonshot-v1-32k-vision-preview`, previously flagged as an offline guess) was confirmed against the
  provider's live model list **and** by a real content-parts round trip that the API accepted and
  `parse_extraction` handled cleanly. All 7 bots restarted onto the new code and stayed error-free.
- Tests: `tests/utils/test_codes.py` (new), `tests/orders/test_counter_sales.py` (new — extraction
  parsing hardest of all, since model output is untrusted input; stock-write + discrepancy paths;
  vision-model-override assertion), `tests/products/test_addproduct_flow.py` (new, 12-step flow +
  button entry), plus extensions across `orders/reports/telegram_bot/tenants/escalations` test files
  and `tests/audit_suite/test_shopowner_isolation.py` (every new shop-owner action re-tested for
  cross-tenant refusal before any data access). **487 total green**, zero warnings, all 9 module
  self-checks pass (was 306 at Stage 12c close, +181 across migration 010 + all 6 phases).

### Stage 12e — Shop & Shop-Owner web dashboard, P1+P2 (separate repo), 2026-07-19

A second, independent codebase — `mobile-shop-and-shop-owner-dashboard` (Next.js App Router,
Tailwind v4, deployed to Vercel) — giving keepers and shop owners a browser UI alongside the 7
Telegram bots. Same Supabase project, same tenant rules; this backend gained only one migration
and zero code changes. Full plan (design system, route map, bridge-API spec) lives in that repo's
`PLAN.md`; this entry records what it means **from this repo's side**.

- **`migrations/020_dashboard_users.sql` (applied live)** — `dashboard_users` (§10-DATA-MODEL) maps
  a Supabase Auth login to `role='keeper'`+`shop_id` or `role='owner'`+`client_id`. No self-signup;
  `scripts/seed_dashboard_users.py` (new) applies the migration and provisions the first two logins
  (`keeper1@shop.local`, `owner@techstore.local`) against the real Client A / Shop 01 rows. Numbering
  starts at 020 (011–019 reserved, so a parallel backend migration can never collide with the
  dashboard's own numbering).
- **P1 (read-only)** — the dashboard's `lib/scope.ts::getScope()` re-implements this repo's tenant
  guard: a keeper sees exactly one shop, an owner sees every shop of their `client_id`, and an
  unknown/foreign resource id returns the **identical 404** as `bot._own_shop`/`get_product`/
  `get_rider` — verified live by requesting another shop's chat transcript and order-detail URL
  while logged in as the keeper login (both 404'd). `lib/period.ts` ports `reports.service.parse_period`
  (Asia/Dubai, no DST) byte-for-byte; `lib/profit.ts` ports `orders.service.profit_summary` +
  `merge_counter` including the counter-sales discrepancy exclusion.
- **P2 (mutations)** — every server action is a line-by-line port of its Python twin, not a
  reinterpretation: `confirmOrder`/`rejectOrder`/`advanceDelivery`/`assignDelivery`/`cancelOrder` mirror
  `orders/service.py`'s guards (single-step fulfilment chain, atomic `decrement_stock` RPC, mandatory
  cancel remarks, rider push with working Accept/Not-received buttons); `approvePrice`/`denyPrice`
  mirror the `0 < price ≤ list` bound; product actions mirror `products/service.py`'s validators and
  tenant guard; `reconcileCod` reproduces the exact previous/today/handover/remaining trail math and
  pushes the rider the same-shaped receipt. Every mutation reuses the **bot's own audit action codes**
  (`kconf`/`krej`/`kdup`/`kappr`/`kcust`/`kdeny`/`kasgr`/`krec`/`kneg`) with `actor="dashboard:{email}"`,
  so `reports.service._HUMAN_ACTIONS`/`format_activity` humanize a dashboard action for free — no
  changes needed on this side of the fence.
- **Known gap (until P4's bridge):** a dashboard mutation calls the shop's real bot tokens directly
  (`https://api.telegram.org/bot{token}/sendMessage`) and archives the turn to `messages`, but it does
  **not** write to this backend's Redis (`escalations/context.py::remember`) — that's a separate
  process. So the AI's 25-turn working memory does not yet learn about a dashboard-side confirm/
  reply/reconcile. Closing this needs the bridge API (P4) or a shared Redis instance; until then, treat
  it like any other out-of-band shop action (e.g. a manual DB edit) — the AI catches up next time it's
  told, same as it already does for shopkeeper Telegram replies during an escalation.
- **Live-verified, not just built:** logged in as both the keeper and owner test accounts; created a
  manual draft order through the dashboard, confirmed it (stock atomically decremented, ✅ Telegram
  message delivered to the real customer account, `kconf` audit row), advanced it to packed, cancelled
  it with remarks (stock restored, remarks visible in the same place `_cancel_remark` reads them from),
  and flipped `/negotiation` off→on (both audited as `kneg`) — all confirmed against the live DB via
  this repo's own `mcp_servers/supabase_server.py`, not assumed from the dashboard's UI alone.

### Stage 12f — dashboard→AI Redis relay + dashboard P3 (POS/invoices/IMEI), 2026-07-19

Two follow-ups on Stage 12e, same day, both driven by the dashboard side but landing schema and
logic **in this repo** (the dashboard has no migrations of its own — see the corrected note in
`10-DATA-MODEL.md`).

- **AI-relay fix (migration `021_message_relay.sql`, applied live).** Closes the "known gap" Stage
  12e shipped with: `messages` gains `relay_pending boolean default false` (partial index on
  `(shop_id, identity, created_at) where relay_pending`). The dashboard's `notifyCustomer` now sets
  it true on every archive row it writes. `escalations/context.py` gained `sync_relay(redis, shop_id,
  identity)` — drains pending rows for one conversation into the Redis session via a raw `rpush`
  (NOT `remember()`, which would duplicate the archive write already done by the dashboard) and
  clears the flag; best-effort, swallows all exceptions. `ai/orchestrator.py::_replay` calls
  `sync_relay` before loading history, so it runs on every customer turn with zero new call sites
  elsewhere. **No bridge endpoint needed** — this runs inside the existing bot process the moment it
  ticks, not through an API call.
- **Dashboard P3 — POS + UAE tax invoices + IMEI tracking (migration `022_pos_invoices.sql`, applied
  live).** Built after web research into UAE regulation (Consumer Protection Law: dated Arabic
  invoice mandatory; FTA: simplified tax invoice ≤ AED 10,000, full invoice with customer
  name+address above; e-invoicing mandate is B2B/B2G-only through 2027, B2C excluded — no
  ASP/Peppol integration needed yet) and niche cell-phone-shop POS features (IMEI/serial tracking is
  the top differentiator among CellSmart/CellStore/Cellivo-class systems). Owner decision: defer
  repair tickets and trade-ins to a later phase; IMEI **is** in scope, compulsory at sale for
  Mobile/Tablet categories, capturable either at stock intake or late (typed at the point of sale).
  - `counter_sales` (the **real**, already-live table from migration 010 — the dashboard's original
    plan proposed re-creating it, corrected during recon) extended: `quantity` check loosened to
    `!= 0` so a **negative row is a void reversal** (`orders.service.merge_counter`/`counter_totals`
    are plain sums, so a void nets its sale out automatically — zero Python changed to support it,
    pinned by a new regression test), `recorded_by` defaults `0`, new `sold_by`/`payment_method`.
  - New `product_units` (IMEI ledger, `unique(shop_id, imei)`, `in_stock|sold`) — `products.quantity`
    stays the sole stock source of truth; the bots never read this table.
  - New `invoices` + `invoice_counters` + `next_invoice_number(p_shop)` RPC — **per-shop** sequential
    numbering (a global sequence would leak one shop's sales volume to another via the gaps), same
    row-lock-increment pattern as `decrement_stock` (migration 003).
  - `shops` gained `trn`/`invoice_name`/`invoice_address`; `products` gained `barcode`.
  - `reports/service.py::_HUMAN_ACTIONS` gained `dcsale`/`dvoid`/`dinv` so the owner's activity log
    and the shop-owner's 📋 Logs render dashboard POS actions as sentences, same as every other
    dashboard action since Stage 12e.
- **Live-verified, not just built:** a real POS sale on the live dashboard — IMEI compulsory
  (rejected without one, accepted with one), stock `6→5`, invoice `INV-000001` with VAT exactly
  `161.86` on a `3,399` sale (`×5/105` fils-exact); void → stock `5→6`, reversal row, invoice kept
  (append-only); a second shop-scoped invoice number (`INV-000002`) issued from an unrelated
  delivered-order flow proved the per-shop sequence is independent per shop. Every DB side-effect
  checked directly (`mcp_servers/supabase_server.py`), not assumed from the dashboard UI. Backend
  suite: 497 green (was 487 at Stage 12e close, +10 across the relay + `_HUMAN_ACTIONS` tests).

### Stage 12g — dashboard P4 (Shop logs + reply/handover + exports + analytics), bridge eliminated, 2026-07-20

The dashboard's owner surface, completed **without** the Cloudflare-Tunnel bridge API its own PLAN.md
had scheduled. The insight during design: every feature the bridge existed for is already reachable
without it, so the whole ~12-endpoint FastAPI addition + `INTERNAL_API_TOKEN` + cloudflared ops burden
was cancelled. Backend changes this stage are small and surgical:

- **`escalations/service.py::still_frozen(redis, shop_id, identity, client=None)`** — a DB-verified
  freeze check. The dashboard's "Return to AI" button only sets `pending_escalations.resolved_at`
  (it has no Redis); `still_frozen` returns fast when Redis says *not* frozen, and when Redis *does*
  say frozen it confirms against the open-escalation row, lazily `unfreeze()`-ing when the dashboard
  already closed it. On a DB error the freeze **stands** (never hand an escalated customer back to the
  AI by accident). `messaging/pipeline.py` step 4b now calls it instead of the bare `is_frozen`.
  Same "DB is authority, Redis is the hot cache" shape as `sync_relay`.
- **`reports/service.py::_HUMAN_ACTIONS`** += `dreply` ("replied to customer {0} from the dashboard"),
  `dhandover` ("returned customer {0} to the AI"), `dedit` ("edited product #{0}: {1}" — carries a
  price/stock diff summary), `counter_sale` (the bot's own code, previously rendered raw), `kprodadd`
  ("added product #{0}").
- **Bot add-product flow** now writes a `kprodadd` audit at completion, so catalogue additions made on
  the keeper bot also appear in the owner's activity log / dashboard Shop logs (the dashboard already
  audited its own product creates).
- Dashboard-side (separate repo): **Shop logs** owner-gated tab (activity/cancels/discounts, price-change
  `dedit` diffs, category+shop+period filters, actor-name resolution), **chat reply + handover**
  (`actions/chats.ts`, Telegram-direct + `resolved_at`), **CSV exports** (orders/riders/logs, built
  in the dashboard, no signed URLs), **analytical Reports** (daily trend, channel/payment split,
  per-shop table, product performance + slow movers, VAT, cancels/discounts).
- **Live-verified:** owner login → a `1500→1350` price edit renders "Price: AED 1,500 → AED 1,350" in
  Shop logs with time/actor/shop; keeper login → no Shop logs nav entry and `/logs` returns 404;
  activity CSV downloads with the diff column; Reports money equals backend `profit_summary`+`merge_counter`
  to the dirham (11,397 / 8,800 / 2,547). Backend suite: **502 green** (was 497; +5 across `still_frozen`
  and the humanizer tests).

## 🔵 In progress

- _(nothing in this backend repo — gap-fix wave complete, awaiting Stage 13. The web dashboard is a separate repo: P1+P2+P3+P4 all done and live-verified as of Stage 12g.)_

## ⏭️ Next up (Stage 13 — WhatsApp/Twilio cutover, §1; the dashboard is feature-complete through P4; + small tails)

1. **Stage 13:** activate the real Twilio path (was mocked, ADR-002) — **this is where `celery_worker` finally gets a producer**; Twilio **outbound** client (the worker currently discards `result.reply`, `ponytail:` at `tasks.py:42`); wire `self.retry` on the pipeline's `locked` action; run the 300+-concurrent load test; owner cutover checklist.
2. **Deploy-time (Stage 12 tail):** flip `AI_PROVIDER=openai`; add CI now that the repo is a real git repo (Stage 12b/c/d note: it wasn't at Stage 12 close — check current state before assuming).
3. **Stage 10 §12 tail (optional):** `/owner usage` (Redis-today + `usage_daily`-past merge), `/owner shop <id>`, shopkeeper `/report daily|inventory_low|top_products`.
4. **Reported, not built (low severity — see Stage 12b):** structured `delivery_date` column, customer phone separated from address, attack-forensics snapshot including the triggering message, DB-level `selling_price >= cost_price` guard.
5. **Web dashboard (separate repo `mobile-shop-and-shop-owner-dashboard`) — P1+P2+P3+P4 all done, live-verified.** Full read UI, every mutation, POS + tax invoices + IMEI tracking, and now (Stage 12g) the owner surface: Shop logs transparency feed, dashboard chat reply/handover, CSV exports, analytical reports. **The bridge API is cancelled** — the dashboard sends Telegram directly and reads/writes the DB directly, so no internal FastAPI endpoints or Cloudflare Tunnel are needed (see that repo's `PLAN.md` §3.3 and Stage 12g above). Deferred by owner decision, not yet scheduled: repair tickets + trade-ins (own phase); ASP/Peppol e-invoicing (B2C excluded from the 2027 mandate).
6. **Shop editing for existing products** (min_qty especially) — `/addproduct` sets `min_qty` on new rows; there is no edit path for the ~10 products that predate migration 010 (they default to `0` = alerts off). **Now moot for shops using the dashboard** — the dashboard's `/inventory/[id]` edit page (Stage 12e P2) covers every field including `min_qty`.

> **Q-012 / Q-013 are open** (`12-OPEN-QUESTIONS.md`): SPEC §4's ranking formula contradicts itself (boost as hard sort key vs relevance multiplier); SPEC §4 literally excludes brand/model from search. Both implemented with the sensible reading — **don't silently re-decide them.** **Q-006 (rider model) and Q-014 (`/productstats`) are now resolved** — see Stage 12b/12d above.

> **Folder rule:** create whatever folders a capability needs (organized by purpose, no loose root files) and register each in `08-FILE-MAP.md`. See `AGENTS.md` §5.
> **Pipeline naming:** SPEC §9 enumerates **7 ordered steps**, not 9 — docs historically said "9-step"; treat "SPEC §9 pipeline" as the 7-step list.

## 🧱 Missing infrastructure — the 4 core services (SPEC §14 line 231)

SPEC §14 mandates `Docker Compose: api, celery_worker, celery_beat, redis, flower`. All five exist in `docker-compose.yml`. **Only `redis` is doing real work.** Audited 2026-07-08 against the code, not the compose file.

| Service | Doing today | Missing | Stage |
|---|---|---|---|
| **redis** | 🟢 session, freeze, usage meter, quarantine/bypass/blacklist/rate, Celery broker+backend, **per-session lock + MessageSid dedup (Stage 11 ✅)** | — | ✅ |
| **api** (FastAPI) | 🟢 real `/health` (Stage 10 ✅) — runs `reports.health.check_health`: DB, Redis, LLM, Twilio, Celery workers, active convos, quarantined; 503 when unhealthy. Plus `/webhook/whatsapp` (real sig-verify, **mocked channel** till Stage 13). | — | ✅ |
| **celery_worker** | 🔴 one task (`process_whatsapp_message`), **zero callers** — its only caller is the mocked WhatsApp webhook | a live producer; and it **discards `result.reply`** (`ponytail:` at `tasks.py:42`) — no Twilio outbound client exists | 13 |
| **celery_beat** | 🟢 **two jobs (Stage 10 ✅).** `flush_usage_counters` hourly (`crontab(minute=15)`) + `health_check` every 60s → pages the owner (`send_to_owner`) on failure. | — | ✅ |

**Three of the four are hollow by design, not by accident** — ADR-002 (Telegram-first) means the WhatsApp producer stays mocked until Stage 13, so `api` and `celery_worker` have nothing to carry. That is the plan working.

> ✅ **The billing leak is closed.** `pipeline.py` still increments `usage:{client}:{shop}:{day}:messages` on every message (2-day TTL as a safety net), but `flush_usage` (`tasks/tasks.py`, on the hourly beat) now drains **completed days** into `usage_daily` and deletes the key. Only past days are flushed — `upsert_usage` overwrites the row's count, and today's counter is still incrementing, so draining it mid-day would lose later messages. Live-verified: seeded a real Redis counter → flush → `usage_daily` row → cleaned up. The remaining Stage-10 beat entry is the 60s health check.

Notes for whoever builds these:
- `beat_schedule` is **~15 lines of config in `celery_app.py`**, not a module — kept that way (both entries inline). Don't scaffold a package.
- ✅ **Done (Stage 10):** one health-checker (`reports/health.check_health`), two callers (`/health` endpoint + `health_check` beat task). The beat pages the owner via `send_to_owner` (NOT `alert_owner` — its signature is shop/customer-scoped, wrong shape for a system-wide failure). Don't add a second health implementation.
- ✅ **Done:** worker liveness = `celery_app.control.ping(timeout=1)` inside `reports.health._worker_count` — the 60s health tick pings the worker, the only exercise `celery_worker` gets before Stage 13.
- `/flower` needs **no FastAPI route**: `docker-compose.yml:41` already passes `--basic-auth`. Add a proxy mount only when a reverse proxy exists.
- The Stage 11 Redis keys (`lock:session:*`, dedup) are `SET NX EX` one-liners inside `pipeline.py`. Correctly deferred — single-threaded Telegram testing cannot race.

## 🟥 Blocked / needs human

- Q-003 Supabase creds ✅ resolved + migration applied — real project `uwlczgwlkqlflpveeykj`; `001_init.sql` pushed via the Supabase MCP server (`mcp_servers/supabase_server.py`, ADR-007); 12 tables + seed + RLS verified live; `SupabaseTenantRepo.list_shops()` reads 3 shops. **RLS still permissive scaffold** (ponytail marker — tighten later).
- Q-005 LLM key ✅ resolved — OpenRouter key wired (`AI_PROVIDER=openrouter`, `AI_MODEL=moonshotai/kimi-k2`); `LLMClient.is_configured=True`; real chat verified 200. `chat()` impl still Stage 4.
- 🟥 **Shopkeeper Telegram IDs are placeholders — escalations reach nobody.** The live `shopkeepers` table holds `telegram_id = 100000001` (from the `001_init.sql` dev seed). Verified live: `escalate()` wrote the row, froze the AI, and then reached **zero** staff — correctly firing the "no shopkeeper reachable" owner alert. Before any end-to-end escalation test, seed the real Telegram user ids of the people who will answer, and have each of them press `/start` on their shop's **keeper** bot (a bot cannot message a user who has never started it).
- ~~Q-006 rider model~~ ✅ **resolved (Stage 12b, 2026-07-12)** — owner onboards riders (1+/shop), one global rider bot, `/assigndelivery` sets `orders.rider_id`; `/exportrider` now produces real data.
- **Telethon `.session` files** — create the 2 account sessions once (one-time phone login) before Stage 2/3 end-to-end tests.

## Anti-patterns / pitfalls (don't redo these)

- **Business model:** the owner is the **service provider** running an automation company; shops are **clients** (NOT branches of one retail chain). Docs (01/02/09) were corrected to this framing. Architecture/ADRs unchanged — multi-tenancy already fits.
- **httpx pin:** do NOT pin `httpx` in `requirements.txt` — supabase 2.10.0 requires 0.27.x; let it resolve. (Was fixed this stage.)
- Module-level `pytestmark = pytest.mark.asyncio` warns on sync tests — decorate async tests individually instead.

## Recent change log

| Date | Stage | Summary | By |
|------|-------|---------|----|
| 2026-07-07 | 0 | Foundations: docs, ADRs 1–4, scaffold, schema, config, LLM stub | Stage 0 |
| 2026-07-07 | 0 | ADR-005 testing topology; dev deps; customer_simulator harness | Stage 0 |
| 2026-07-07 | 1 | db repo interface + in-memory + supabase/redis factories; tenants models/service/auth; 17 tests green; /health boots | Stage 1 |
| 2026-07-07 | 1 | ADR-006 client layer + usage_daily; schema+seed updated; Client model; client+usage tests; 23 tests green | Stage 1 |
| 2026-07-07 | 1 | Engineering ethos (caveman/karpathy/ponytail) baked into docs + AGENTS + CONVENTIONS; PONYTAIL-DEBT ledger; 4 markers placed | Stage 1 |
| 2026-07-07 | 2 | telegram_bot: owner_only gate + /pauseshop /resumeshop /shopstatus + shopkeeper stubs + build_application + run_bot.sh; userbot skeleton; 10 bot tests (33 total) | Stage 2 |
| 2026-07-07 | 2 | ADR-005 revised → 5 bots; per-shop keeper+customer bots; run_all_polling multi-bot runner; real tokens wired into .env (gitignored) + txt files deleted; Shop model+migration columns; 4 multi-bot tests (37 total) | Stage 2 |
| 2026-07-07 | 2 | Real Supabase project + OpenRouter→Moonshot creds wired into .env (gitignored); ADR-004 revised; settings: openrouter provider + supabase_mgmt_token; Supabase REST 200 + LLM chat 200 verified; key.txt deleted; Q-003/Q-005 resolved | Stage 2 |
| 2026-07-07 | 2 | ADR-007 Supabase MCP server (mcp_servers/); 001_init.sql APPLIED to live DB via MCP (12 tables+seed+RLS verified); SupabaseTenantRepo reads live DB (3 shops); scripts/apply_migration.py; mcp dep pinned (no [cli]) | Stage 2 |
| 2026-07-07 | 2 | Per-shop bot tokens persisted to live shops rows (seed_shop_bots.py); SupabaseTenantRepo._row_to_shop maps telegram_* fields; run_bot.sh now runs bots against live DB; suspend/resume persistence verified; 2 ponytail markers resolved | Stage 2 |
| 2026-07-07 | 3 | messaging/pipeline.py (SPEC §9, channel-agnostic); customer bot wired to pipeline (echo removed); whatsapp/webhook.py (Twilio sig-verify + 200-immediate, mocked); tasks/ Celery skeleton; db/factory.py; Redis usage meter; deleted dead resolve_shopkeeper NameError; 11 new tests (48 total) | Stage 3 |
| 2026-07-07 | 4 | LLMClient.chat() implemented (tool-calling + retry-once); llm/functions.py + prompts.py; products/models.py + search.py (SPEC §4 ranking); ai/orchestrator.py (chat loop, escalation short-circuit, boost never serialized); pipeline step 7 wired to real AI; ADR-008; Q-012/Q-013 logged; live Moonshot verify passed; fixed hidden network call in task test; 12 new tests (60 total) | Stage 4 |
| 2026-07-07 | 5 | products/service.py: tenant guard + boost/tag/feature mutations + trust-boundary validation (tag whitelist); 6 commands wired on keeper bot; **fixed blocking sync-Supabase-in-async in search.py** (would stall all bots); /productstats deferred → Q-014; 10 new tests (70 total). `/addproduct` + media = part 2 | Stage 5 |
| 2026-07-08 | 5 | pt2: migrations/002_storage_buckets.sql (**shop-media bucket didn't exist** — found via the project's Supabase MCP, applied through it); products/media.py; create_product + field parsers; addproduct_flow.py (11-step PTB ConversationHandler); wired on keeper bot; live end-to-end verify incl. cross-shop denial on the real DB, cleaned up after; 5 new tests (75 total) | Stage 5 |
| 2026-07-08 | 5 | test catalogue (tests/fixtures/catalog.py + scripts/seed_test_catalog.py, 8 scenario products on the owner's real media); **found Q-015**: AI answered "cheapest" wrongly because search was boost-ranked + truncated with no price signal | Stage 5 |
| 2026-07-08 | 5 | **Q-015 fixed (ADR-008 rev. 2)**: sort=price_asc/price_desc + max_price_aed; price ordering ignores boost; superlative rule in prompt; tool description warns a default search can't answer superlatives; _SYNONYMS (phone→mobile, cheap→budget); orchestrator validates sort enum. Live-verified: cheapest=2499, under-2600 respected, most-expensive=4699. 8 new tests (83 total) | Stage 5 |
| 2026-07-08 | 6 | escalations/{service,context}.py + telegram_bot/notify.py; pipeline step 4b freeze (shares one forward path with bypass); AI now **multi-turn** via Redis session replay; /reply + /handover on keeper bot; **ADR-009 debt paid** — the promised specialist is really summoned, owner really paged; per-shop tenant guard on /reply,/handover; owner paged when no shopkeeper reachable; 24 new tests (111 total); live-verified escalate→freeze→handover→resolve; **found: live shopkeeper telegram_id is a seed placeholder** | Stage 6 |
| 2026-07-09 | 7 | security/{detectors,service}.py — 6 attack patterns (pure) + auto-quarantine (1h) + security_incidents last-25 snapshot + blacklist (Redis+DB) + bypass; pipeline steps 3/4/6 wired live, key strings centralized in security (writer/reader can't drift); owner cmds /investigate /quarantine_lift /quarantine_extend /blacklist /forward_to_shop /bypass_ai /bypass_remove; DB-write-failure still holds block + alerts; false-positive recovery via owner; 31 new tests (143 total) | Stage 7 |
| 2026-07-09 | 7 | **Memurai installed** (native Redis on :6379, Windows service, auto-start) — real Redis local, no Docker needed for tests; Docker Desktop also installed (needs reboot to finish). Security path live-verified through real Redis + live Supabase incident + real owner Telegram alert. Ponytail full skill installed globally (v4.8.4 + 6 invocable skills) | tooling |
| 2026-07-10 | 8 | orders/{models,service}.py + reports/service.py — profit engine (pure `line_profit`, cost = cost_price×qty), `create_order` (tenant-guarded, only orders writer), `profit_summary` range aggregation; `/profit [period]` (keeper) + `/owner profit [all\|compare\|shop]` (new /owner dispatcher); live-verified real order→report→cleanup; **Q-017 raised** (no order-placement flow specced); 10 new tests (153 total) | Stage 8 |
| 2026-07-10 | 8b | **Hybrid order booking (Q-017 → ADR-010)**: `place_order` tool (AI drafts) + `/orders` `/confirmorder` `/rejectorder` (human confirms); migration 003 (draft status, order_number, `decrement_stock` RPC); atomic stock decrement, no premature customer message, draft doesn't freeze AI; live-verified draft→confirm→stock 5→4→restore | Stage 8 |
| 2026-07-10 | 8c | **Negotiation reworked to human-in-the-loop (ADR-010 rev.1)**: secret `min_price` floor removed (migration 004 drops it); `request_price` tool + `/approveprice` `/custom` `/denyprice` `/negotiation on\|off`; `price_requests` table; per-shop toggle checked fresh (off = no discount); draft applies only shop-approved price; shop notices show cost/list/discount/margin; live-verified request→custom→draft discount + off-blocks; net +4 tests (164 total) | Stage 8 |
| 2026-07-10 | 9 | **Excel order export (SPEC §10)**: `utils/excel.py` (pure openpyxl builder — §10 columns, `#2563EB` header, borders, auto-width, RAM/Storage from specs, net selling price) + `utils/storage.py` (upload → private `shop-reports` + 24h signed URL); `orders.export_orders`/`export_rider` (+ `orders_for_export`/`rider_orders_for_export`); `/exportorders [period] [detailed]` + `/exportrider <id> [period]` on keeper bot; migration 005 (shop-reports bucket) applied live; settings.supabase_reports_bucket; **live round-trip verified** (query→workbook→upload→download signed URL→valid xlsx→cleanup); rider sheets empty until rider-assignment (Q-006); 4 new tests (168 total) | Stage 9 |
| 2026-07-10 | 10 | **Beat usage-flush — billing leak CLOSED (ADR-006)**: `celery_app.conf.beat_schedule` (`flush_usage_counters` hourly, `crontab(minute=15)`, UTC) — `celery_beat` did nothing before; `tasks.flush_usage` core drains **completed-day** `usage:*` Redis counters → `usage_daily` via `getdel`+`upsert_usage` (today's key skipped: overwrite-upsert would lose later msgs; idempotent; malformed skipped); `pipeline.USAGE_KEY_PREFIX`+`parse_usage_key` (writer owns format+parse); **live round-trip verified** (seed real Redis counter→flush→usage_daily row→cleanup); 4 new tests (172 total) | Stage 10 |
| 2026-07-10 | 10 | **Health check + `/health` + owner dashboards (§12/§13)**: `reports/health.check_health` (one checker: DB/Redis/LLM/Twilio/Celery-workers + active-convos/quarantined metrics) → real `GET /health` (503 unhealthy) **and** `health_check` beat task (60s → `send_to_owner` on failure); `repo.health_check()` cheap DB probe; `/owner dashboard\|health\|escalations\|security\|audit` on the owner dispatcher (+ `escalations.count_open`/`list_open`, `security.recent_incidents`); live-verified real check + real `-P solo` worker on Memurai (celery down→ok as worker starts/stops); 5 new tests (177 total) | Stage 10 |
| 2026-07-10 | 11 | **Concurrency/reliability hardening (§11)**: `process_message` wrapped in per-session lock (`lock:session:{shop}:{id}` `SET NX EX 30`, released in finally, `locked` on contention) + MessageSid dedup (`dedup:{sid}` `SET NX EX 300`); **lock-first/dedup-second** so a `locked` retry (Stage 13 Twilio) isn't deduped away; steps 2–7 moved to `_dispatch`; Celery `task_acks_late`+`prefetch=1`; retry-once+fallback + 200-immediate already done; live-verified on Memurai (held→locked, normal→freed, dup sid→dropped TTL300); 4 new tests (181 total) | Stage 11 |
| 2026-07-10 | 12 | **Production hardening (§16)**: `audit/service.py` (`record`/`recent` → `audit_logs`, best-effort never-raise) wired at the `owner_only`+`keeper_command` wrappers (every privileged action logged with actor, one place); `/owner audit` now reads real rows; `core/logging.py` `setup_logging()` (structured lines) on bot + API entrypoints; live-verified audit write→read→cleanup. **Deferred (deploy-time):** CI (no git repo), `AI_PROVIDER=openai` flip, prod Docker override. 4 new tests (185 total) | Stage 12 |
| 2026-07-11 | audit | **Full security audit (`/vibe-security-audit`) + fixes — see `docs/SECURITY-AUDIT.md`.** Fixed: **[CRITICAL] keeper bots had no staff auth** (anyone finding a keeper bot could `/confirmorder`, `/approveprice`, `/exportorders` customer PII) → fail-closed `_keeper_auth_gate` (registered shopkeepers + owner only, covers `/addproduct` too); **[HIGH] RLS permissive/off** → migration 006 locks the data API to service-role (RLS on all tables, scaffold policies dropped, anon revoked — verified anon gets 42501); **[HIGH] no AI cost ceiling** → per-customer daily cap (`ai_daily_msg_cap`, `security.bump_daily`); **[MED] bot tokens logged** (httpx URLs) → silenced to WARNING; **[MED] money bounds** → `request_price` >0, `approve_price` 0<price≤list; **latent NameError** (`logger` undefined in orders/service.py) fixed. Verdict 🟢 after fixes. 4 new tests (193 total). Live-verified keeper gate + RLS + token-logging | audit |
| 2026-07-10 | live | **Negotiation: duplicate price requests + AI unaware of approval (found on Telegram: haggle → shop approved 1350, then "book it" → AI re-asked, made pending #3/#4, said "still waiting").** Same root theme as the id bug — approvals are system-sent (`send_to_customer`), never in the AI's session. Fixes: (a) `request_price` is now idempotent — `already_approved` → steer the model to `place_order`; a `pending` request → reuse, no duplicate (`_pending_price_request`); (b) `approve_price`/`deny_price`/`confirm_order` now `_remember_to_customer` (record the outcome as an assistant turn) so the AI knows and doesn't re-ask; (c) prompt: never call `request_price` twice, and on `already_approved` go straight to `place_order`. **Also fixed a latent NameError** — `logger` was used in `orders/service.py` but never defined. Live-verified: haggle→dedup→approve 1350→already_approved→draft applies 1350 (discount 150). +2 tests (189 total) | live-fix |
| 2026-07-10 | live | **Booking failed → false escalation + owner page (found on Telegram: "ok book it" → details → "empty model response").** Root cause: the session replays only text turns, so the real product UUIDs from an earlier `search_products` were gone by the booking turn; the model **invented** a `product_id` (`prod_redmi_x11_blue`), `place_order` rejected it, the model retried with more fake ids, the tool loop ran out → empty content → handoff + alert. Fix: `orchestrator._id_reference(shop)` injects a hidden "name → real id" table (all in-stock) as a system message **every turn**, so `place_order`/`request_price`/`show_product_media` always use a real id. Never shown to the customer; best-effort (never breaks the turn). Live-verified: "ok book it" → "Asad, Dip 1, 1" → **real draft created**, AI replies "the shop will confirm", no escalation, no owner page. (187 tests green) | live-fix |
| 2026-07-10 | live | **Three fixes from the Telegram walkthrough:** (1) `/productstats` stub said "Stage 8" → real keeper handler with an honest "not tracked yet (Q-014)" message. (2) **AI dumped the whole catalogue** on a vague ask → prompt rewritten to consultative selling: ask 1–2 qualifying questions first, then show ≤3 matched options, recommend when the customer is unsure. (3) **AI said it couldn't share photos** → new `show_product_media` tool + `products.media.signed_urls` + `answer_customer(media_sink=...)` out-param + `PipelineResult.media` + customer bot `send_photo`/`send_video`; bounded 3-round tool loop so it can search→show→answer. Media stays channel-agnostic (URLs; Telegram sends now, Twilio at Stage 13). Live-verified signed image fetch 200. +1 test (187 total) | live-fix |
| 2026-07-10 | live | **Two beat-path bugs found running the full system on Telegram (owner got spammed "health check failed" every 60s):** (1) Celery tasks `asyncio.run` a new loop each tick but reused the **cached** async Redis client → `redis: down: Event loop is closed` after tick 1 → added `db.redis_client.new_redis()` (fresh, loop-local, closed in `finally`) used by `_health_task`/`_flush_task`. (2) `control.ping()` from **inside** a busy solo-pool worker returns 0 → `celery: down: no workers` → beat now calls `check_health(include_celery=False)` (a running beat task already proves a worker is alive; worker-liveness is the external `/health` poll's job). Verified: repeated ticks all `ok`, no page. +1 test (186 total) | live-fix |
| 2026-07-08 | 5 | **LLM provider switched to official Moonshot (ADR-004 rev. 2)**: OpenRouter dropped (owner reported problems). `AI_MODEL=kimi-k2.6` on `api.moonshot.ai`. Probed the API rather than guessing: `.cn` 401s global keys; `kimi-k2.7-code*` are code-only; **`kimi-k2.*` rejects any temperature but 1** (`AI_TEMPERATURE=1.0`, also fixed as the code default). Zero changes to `llm_client.py`/`ai/` — the abstraction held. Re-verified live: tool-calling, escalation, and the full Q-015 suite. `key.txt` consumed into gitignored `.env` and deleted | Stage 5 |
| 2026-07-11 | audit | **8-goal live QA audit** driven end-to-end against the live bots + live DB (not mocks): customer chat, escalation/security, inventory, order/delivery, owner oversight, concurrency/edge, reports/accuracy, UX/errors. Found + fixed the **over-length false-quarantine bug** (a clean >2000-char message tripped the injection detector's length rule — removed from `detect_attack`, handled instead as a friendly `too_long` pipeline reply). Built **fulfilment status**: `orders.advance_delivery` + keeper `/deliveryupdate <#> packed\|shipped\|delivered` (`_is_next_step` pure rule — no skip/backward/off-flow). All 8 goals live-tested on Telegram; results pushed to the owner's chat | audit |
| 2026-07-12 | 12b | **Timezone decided + built**: owner confirmed UAE-only → `reports.service` uses one `DUBAI=+4` day boundary (`parse_period`/`_day`), no per-shop column (would be unused configurability for a single-timezone business) | live-fix |
| 2026-07-12 | 12b | **Rider onboarding + assignment + COD (Q-006 resolved).** New `riders/` module + migrations 007 (`delivery_persons.telegram_id`) and 008 (`orders` COD/custody columns + new `cod_ledger`), both applied live. Owner onboards riders (`/addrider`, `/riders`); rider links Telegram by shared contact (phone-matched); keeper `/assigndelivery` (COD + balance on the card) and `/reconcilecod` (previous+today−handover=remaining, pushed to rider too); global rider bot with `/mydeliveries`, `/accept`\|`/notreceived` (**custody handshake**, write-once — neither side can dispute a handover after), `/deliver` (time→cash→finalize), `/canceldelivery` (remarks mandatory, restocks), `/myreport`. Money is an append-only ledger, balance always re-derived. Live-verified full lifecycle on order #7 (assign→accept→deliver 1500→reconcile 1650→50 AED remaining), every row checked against the formula. 50 new tests (235 total) | Stage 12b |
| 2026-07-13/14 | 12c | **Inline-button UX on every bot + security audit suite + shop-owner bot (7th bot, ADR-006).** `telegram_bot/keyboards.py`+`format.py` (new) — every menu button calls the same service function its matching slash command calls, never a second implementation. `tests/audit_suite/` (new) — tenant isolation vs prompt injection, webhook dedup under concurrency, atomic-stock-decrement race, WhatsApp sanitization, callback tenant isolation, every registered command on every bot smoke-run. Fixed 2 bugs found building it: `/addproduct` confirmation could 400 on shopkeeper-typed Markdown chars (now Telegram HTML, escaped); rider `/start` crashed (`kb` variable shadowed the module alias). **Shop-owner bot**: migration 009 (`clients.telegram_id` + new `messages` permanent chat archive, dual-written alongside the Redis session) applied live; deliberately no security/escalation views (owner: those would scare a client into abandoning the system); full financial/operational visibility instead — profit/orders/inventory/riders&COD/exports/transcripts per owned shop + cross-shop analytics. `scripts/run_bots_live.sh` (pins the venv python explicitly). 306 total green (52 in the new audit suite alone, zero warnings) | Stage 12c |
| 2026-07-19 | 12e | **Web dashboard P1+P2 (separate repo `mobile-shop-and-shop-owner-dashboard`).** Migration 020 (`dashboard_users`, applied live) + `scripts/seed_dashboard_users.py`. P1: Next.js scaffold, Supabase Auth, tenant scope mirroring `_own_shop` (same 404 for foreign/unknown resources, verified live), Dubai-period + profit math ported byte-for-byte. P2: every mutation ported from its Python service twin (orders lifecycle, price requests, product CRUD + media, riders/COD, negotiation), reusing the bot's own audit action codes (`kconf`/`krej`/etc.) so the owner bot's activity log reads dashboard actions for free. Live-verified: draft→confirm (stock decremented, customer Telegram message, audit row)→packed→cancelled (stock restored, remarks recorded); negotiation off→on audited. Known gap until P4's bridge: dashboard sends don't reach this backend's Redis AI session. Zero changes to backend code — one migration only | Stage 12e |
| 2026-07-19 | 12f | **Dashboard→AI Redis relay (migration 021, applied live).** Closes Stage 12e's "known gap": `messages.relay_pending` flag + `escalations.context.sync_relay` (drains pending rows into the Redis session via raw `rpush`, called from `orchestrator._replay` every customer turn) + the dashboard's `notifyCustomer` sets the flag. Best-effort, no bridge/endpoint needed. 2 new tests | Stage 12f |
| 2026-07-19 | 12f | **Dashboard P3: POS + UAE tax invoices + IMEI tracking (migration 022, applied live), after UAE-regulation + niche-POS market research.** Extends the real `counter_sales` (migration 010) with a `quantity != 0` void-reversal rule (`merge_counter`/`counter_totals` net it out for free — regression test added), `sold_by`/`payment_method`; new `product_units` (IMEI ledger, stock-count stays authoritative), `invoices` + `invoice_counters` + `next_invoice_number` per-shop-sequential RPC, `shops.trn/invoice_name/invoice_address`, `products.barcode`; `_HUMAN_ACTIONS` gained `dcsale`/`dvoid`/`dinv`. Repairs/trade-ins deliberately deferred (owner decision). Live-verified full sale→void cycle, VAT math exact, sequential per-shop invoice numbers. 8 new tests (497 total) | Stage 12f |
| 2026-07-20 | 12g | **Dashboard P4 (Shop logs + chat reply/handover + CSV exports + analytics) — bridge API eliminated.** The planned Cloudflare-Tunnel bridge (~12 FastAPI endpoints + `INTERNAL_API_TOKEN` + cloudflared ops) was cancelled: the dashboard sends Telegram directly and reads/writes the DB directly. Backend changes (this repo): `escalations/service.py::still_frozen` (DB-verifies a Redis freeze so the dashboard's `resolved_at` write unfreezes the AI lazily on the customer's next turn — `pipeline.py` step 4b uses it), `_HUMAN_ACTIONS` += `dreply`/`dhandover`/`dedit`/`counter_sale`/`kprodadd`, and a `kprodadd` audit at the end of the bot's add-product flow. Dashboard (separate repo): owner-gated Shop logs (activity/cancels/discounts + price-change diffs), `actions/chats.ts` reply+handover, CSV exports, analytical reports. **No migration** — `dedit` diffs ride the existing `audit_logs.detail` jsonb. Live-verified: price 1500→1350 edit shows "Price: AED 1,500 → AED 1,350" in Shop logs, keeper `/logs` 404s, reports money equals backend to the dirham. 5 new tests (502 total) | Stage 12g |
| 2026-07-16 | 12d | **Gap-fix wave: migration 010 (applied live) + 6 phases, from a structured 14-item live-use report.** Real bug fixed: `list_price_requests` selected a nonexistent `identity` column (live column `phone`) — every `/pricerequests` press 500'd as "Internal error." Friendly reference codes (`utils/codes.py`; `PR0001`/`rider001`, migration-010-backfilled, `get_product_by_ref`/`bot._resolve_product`/`_resolve_rider` are now the one choke point every keeper command routes through) replace raw UUIDs everywhere a human types one. Real `/productstats` (folds orders against the catalogue — was Q-014's honest stub, **now resolved**), low-stock alerts (`orders.notify_low_stock`, fires only after a positive decrement), a 12th `/addproduct` step (`min_qty`) reachable from a button too, a printable 🧾 counter sheet. Platform-owner onboarding (`create_client`/`create_shop`/`create_shopkeeper` existed as repo methods with **zero callers** — now a real ➕ Onboarding menu + `/addclient`/`/addshop`/`/setshoptokens`/`/addkeeper`), owner analytics (top products/cancels+discounts/COD across every shop, reusing the shop-owner bot's own formatters), `escalations.resolve_escalation` made public + a ✔️ Resolve button (`/reply` alone never closed a row). Shop-owner 🗓 date-range orders (its first free-text consumer, re-guards the shop id when text lands) + a 📋 activity log (`_audit` now fires from the mutating inline buttons too, not just slash commands — a button-only keeper previously left zero trail). **Counter (walk-in) sales**: shop fills a printed sheet by hand → shop owner photographs it → vision model (`moonshot-v1-32k-vision-preview`) extracts rows → **owner confirms before anything is written** (man-in-the-middle by design); a discrepancy (sheet says sold, stock says impossible) is recorded flagged, never dropped, never counted as revenue; folds into `/profit` via `merge_counter`. Live-verified: identity backfill (10 products→PR0001-10, 1 rider→rider001, zero nulls), cross-shop code resolution correctly refused, `/productstats` total exactly matched `/profit` total, counter sheet exported real Storage objects, vision model round-trip confirmed against the live provider. All 7 bots restarted onto the new code, zero errors. 181 new tests (487 total), zero warnings, 9/9 self-checks pass | Stage 12d |
