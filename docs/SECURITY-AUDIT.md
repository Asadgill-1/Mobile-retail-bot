# Security Audit — Multi-Shop Retail Chatbot

**Date:** 2026-07-11 · **Scope:** full codebase (`src/`, `config/`, `migrations/`, `mcp_servers/`, `scripts/`) + live Supabase project `uwlczgwlkqlflpveeykj`
**Method:** vibe-security-audit (OWASP LLM/Agentic + web Top-10), code + live-DB inspection. Not a live penetration test.

## Stack detected
Pure **Python backend** (no browser bundle): FastAPI (`/health`, mocked Twilio webhook) + python-telegram-bot v20 (5 long-polling bots) + Celery (worker + beat). **DB:** Supabase Postgres via the **service-role key** (server-side only). **Auth:** Telegram user-id gates (owner id + per-shop shopkeeper table); no web auth/JWT. **AI:** Moonshot `kimi-k2.6` (tool-calling agent). **Payments:** none (cash-on-delivery). **Deploy:** Docker Compose.

Because there is no client bundle and no anon-key usage, the entire "secrets shipped to browser / public anon key" class (the #1 real-world Supabase breach) does **not** apply here — the only DB path is the service-role backend.

---

## Verdict: 🟡 → 🟢 SHIP-READY after this audit's fixes

One **Critical** (unauthenticated keeper bots) and the RLS posture were the real risks. **Both are fixed and verified live** in this pass, along with three supporting hardening items. No remaining Critical. Residual items are defense-in-depth / ops (documented below).

---

## Findings & fixes (all FIXED + verified this pass)

### [CRITICAL] Keeper bots had no staff authorization — FIXED
- **Where:** `telegram_bot/bot.py` — `keeper_command` wrapped every shopkeeper command but never checked the sender.
- **Attack:** Telegram bots are discoverable by @username. Any stranger who found a shop's keeper bot could run `/confirmorder` `/rejectorder` (manipulate orders + stock), `/approveprice` `/custom` (grant arbitrary discounts — money), `/profit` (revenue/cost/margin), `/addproduct` `/boost`, `/reply` `/handover` (impersonate the shop), and **`/exportorders` — exporting every customer's name, address, and phone (PII breach)**. Across 30 shops, every keeper bot was exposed.
- **Fix:** a fail-closed staff gate (`_keeper_auth_gate`, `TypeHandler` at group −10) runs before every handler on each keeper bot; it allows only the shop's registered shopkeepers (`list_shopkeepers`) plus the global owner, and raises `ApplicationHandlerStop` for anyone else. Covers all commands **and** the `/addproduct` conversation. Fails closed on lookup error.
- **Verify:** unit test `test_keeper_auth_gate_blocks_strangers_allows_staff_and_owner`; live — staff `5215780245` allowed, stranger `999999` blocked against the live DB.

### [HIGH] Supabase RLS permissive / disabled — FIXED
- **Where:** live DB — `shops` (holds bot tokens), `clients`, `price_requests`, `blacklisted_phones` had RLS **off**; all other tables had permissive `using(true)` policies.
- **Attack:** if the anon key ever leaked, those tables were fully readable/writable (bot-token theft, billing data, customer phones). (Mitigated today only by the anon key not being shipped.)
- **Fix:** `migrations/006_rls_lockdown.sql` (applied live) — RLS enabled on **every** table, all `using(true)` scaffold policies dropped, `anon`/`authenticated` grants revoked. The service-role backend bypasses RLS and is unaffected; per-tenant enforcement stays at the app layer (`shop_id` on every query), now with the data API sealed behind it.
- **Verify:** live — service-role reads `shops` fine; anon key returns **42501 permission denied** on every table.

### [MEDIUM] Bot tokens / API keys logged in cleartext — FIXED
- **Where:** `httpx`/`httpcore` logged every request URL at INFO — Telegram `getUpdates`/`sendMessage` URLs embed the **full bot token**.
- **Fix:** `core/logging.setup_logging()` raises `httpx`/`httpcore` to WARNING. Verified live — bot startup logs no longer contain token URLs.

### [MEDIUM] Money path missing bounds (business logic) — FIXED
- **Where:** `orders/service.py` — `request_price`/`approve_price` accepted any Decimal.
- **Attack:** a negative/zero requested price, or an "approved" price above list (negative discount → violates the `discount_amount >= 0` DB check → crash), or a prompt-injected negative offer.
- **Fix:** `request_price` rejects offers `<= 0`; `approve_price` requires `0 < price <= list`. Also fixed a **latent `NameError`** (`logger` used but never defined in `orders/service.py`). Tests pin both.

### [HIGH] No per-customer AI cost ceiling — FIXED
- **Where:** `messaging/pipeline.py` — rapid-fire (20/60s) caught bursts, but a sustained flood just under it (e.g. 19/min for hours) would run up the LLM bill; matters at 30 shops.
- **Fix:** configurable per-customer **daily** cap (`settings.ai_daily_msg_cap`, default 1000 — far above any real customer, `0` disables) via `security.bump_daily`; over the ceiling → flat generic reply, no AI call. Test pins it.

---

## Passed checks (verified safe)
- **Secrets:** no hardcoded keys/tokens in source; `.env` gitignored; `.env.example` placeholders only; not a git repo (no history leak); service-role key server-side only; no client bundle.
- **Injection:** supabase-py is fully parameterized (`.eq()`, `.rpc()`); no SQL string interpolation, no `eval`/`exec`/`os.system`/`subprocess`/`pickle`/`yaml.load`. The raw `execute_sql` MCP tool takes developer DDL, not user input.
- **Prompt injection / LLM output handling (OWASP LLM01/05):** attack detector quarantines injection/sql/cross-shop/cred-probe patterns; user text is a separate `user` message; **model output is never fed to SQL/shell/eval** — tool args are validated (UUID parse + tenant guard).
- **Tool privilege (LLM06/ASI03):** AI tools are shop-scoped (`shop.id` from bot context, never user input); the only consequential actions are **human-in-the-loop** (order = draft→shopkeeper `/confirmorder`; discount = shopkeeper `/approveprice`). No autonomous destructive tool.
- **Tenant isolation:** `products.service.get_product(shop_id, …)` guards every product mutation; `ProductNotFound` uses the same message for "unknown" and "another shop's". `create_order`/`create_product` force `shop_id` server-side — no mass-assignment.
- **Authorization angles:** owner commands gated (`owner_only` → `is_owner`); no IDOR (all queries `shop_id`-scoped); no client-side-only enforcement; JWT n/a (no web auth).
- **SSRF:** no server-side fetch of a user-controlled URL. Media URLs are our own signed Supabase URLs; Telegram (not our server) fetches them. LLM base URL is config, not user input.
- **Errors:** no stack traces / DB errors to clients; no `DEBUG=True`/`reload=True`; customer-facing failures collapse to one neutral line (ADR-009).
- **Payments:** none (COD) — no client-side pricing / unverified webhook surface.

---

## Residual / recommended (non-blocking)
- **Provider-side spend limit** (Moonshot dashboard) + per-client plan quotas — the real cost ceiling; the daily cap is a backstop, not a billing system. (Per-client billing = Stage 10/11.)
- **CI:** repo is not a git repo yet — add `.github/workflows/ci.yml` running `pytest -q` once initialized.
- **Real per-tenant RLS** (JWT `shop_id` claim) — only worthwhile if a direct-to-Supabase client is ever added; today the service-role backend + app-layer `shop_id` + the 006 lockdown cover it.
- **Twilio path** (Stage 13): signature verification exists; re-verify on cutover.
- **Shopkeeper onboarding:** staff must be seeded in `shopkeepers` before they can use a keeper bot (now enforced) — wire this into the owner onboarding flow.

## Fix order (all High+ already applied)
1. ✅ Keeper-bot authorization (Critical)
2. ✅ RLS lockdown (High) · ✅ AI daily cap (High)
3. ✅ Token-in-logs (Medium) · ✅ money-path bounds (Medium)
4. ▶ Provider spend limit + CI (ops, recommended)
