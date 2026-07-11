# 02 — Scope

> Source: `docs/SPEC-source.md` §1–§16. This is the MVP = the full spec.

## In scope (v1 / MVP)

- **§1 Multi-tenancy & isolation** — Supabase, `shops` table, `shop_id` FK on all tables, Twilio-number→shop lookup via webhook `To`, Telegram `telegram_id`→shop, `X-Internal-API-Key`. **ADR-006:** `clients` table above `shops` (one client → many shops) + `usage_daily` for per-client usage insight.
- **§2 Shop suspension** — `/pauseshop` `/resumeshop` `/shopstatus`; suspended = WhatsApp auto-reply + Telegram read-only + existing orders continue.
- **§3 AI anti-hallucination + human escalation** — GPT-4o (prod) / Moonshot (test) function-calling-only; out-of-domain detection → `pending_escalations` → notify shopkeeper → freeze AI → `/reply` `/handover` with Redis context.
- **§4 Product inventory + flexible specs** — `/addproduct` 11-step flow; `products` with JSONB `specs`, `boost_level`, `tags`, `is_featured`; natural-language `search_products`.
- **§5 Boosting & tagging** — `/boost` `/unboost` `/tag` `/untag` `/cleartags` `/feature` `/productstats`; AI promotion logic.
- **§6 Profit calculation & reporting** — formula, `/profit` variants (shopkeeper + owner), formatted reports.
- **§7 Intrusion detection & auto-quarantine** — 6 patterns, Redis quarantine, `security_incidents`, owner investigation cmds.
- **§8 Direct-to-shop bypass** — `/bypass_ai` `/bypass_remove`.
- **§9 Message processing pipeline** — exact 9-step order.
- **§10 Order export (Excel)** — openpyxl, Supabase storage, 24h signed URL, `/exportorders` `/exportrider`.
- **§11 Concurrency & reliability** — 200-immediate + Celery, per-session Redis lock, MessageSid dedup, OpenAI retry+fallback.
- **§12 Reports** — shopkeeper + owner report commands.
- **§13 System health** — `/health`, Celery beat 60s, Flower basic-auth.
- **§14 Tech stack** — Python 3.11 / FastAPI / Supabase / Redis / Celery / python-telegram-bot v20 / Twilio / OpenAI+Moonshot / openpyxl / Docker Compose.
- **§15 Database tables** — all 12.
- **§16 Deliverables** — full codebase, schema, AI service, attack detection, Excel, profit, commands, health, audit, Docker, .env, README.

## Out of scope (explicitly deferred)

- **Real WhatsApp/Twilio provisioning during testing** — Telegram-first; WhatsApp mocked until pre-deploy (ADR-002).
- **OpenAI GPT-4o during testing** — use Moonshot (ADR-004); switch at prod hardening (Stage 12).
- **Client self-service / shopkeeper self-management of the platform** — the service provider (owner) operates everything on the client's behalf (SPEC §SYSTEM OVERVIEW). Shopkeepers manage *their shop's* products/orders only.
- **Per-client billing / SLA tracking** — out of MVP scope (see Q-011); the service provider handles billing outside the system for now.
- **Non-AI channels** (web chat, email, SMS beyond Twilio WhatsApp).

## Future scope (planned, later phases)

- Web admin dashboard (beyond Telegram commands).
- Multi-currency support (currently AED — see `12-OPEN-QUESTIONS.md`).
- Rider app / live delivery tracking (rider model is minimal in spec — see open questions).

## Hard constraints

- **Single deployment** serving all 30 shops (SPEC §SYSTEM OVERVIEW).
- **Complete tenant isolation** — never leak cross-shop data (SPEC §1, §7 cross-shop probing).
- **Production-grade** — the owner explicitly requires production grade (SPEC, final note).
- **Never drop messages** — webhook returns 200 immediately, Celery processes (SPEC §11).
