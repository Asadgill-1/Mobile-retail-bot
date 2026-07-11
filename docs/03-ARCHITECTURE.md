# 03 — Architecture

> Source: `docs/SPEC-source.md` §1, §9, §11, §13, §14.
> Detail lives in module docs (`docs/modules/` + each `src/app/<module>/README.md`) and ADRs.

## System overview

```
                 ┌───────────────────────────┐
   WhatsApp ───▶ │  Twilio (1 number/shop)   │ ──┐
  (customers)    └───────────────────────────┘   │  single webhook
                                                  ▼
   Telegram ───────────────────────────────▶ ┌────────────────────────┐
  (owner + shopkeepers)                      │  FastAPI webhook (api)  │
                                             │  - validate Twilio sig  │
                                             │  - 9-step pipeline      │
                                             │  - return 200 immediately│
                                             └─────────────┬──────────┘
                                                           │ enqueue
                                                           ▼
                                                    ┌──────────────┐
                                                    │   Celery     │  celery_worker
                                                    │   tasks      │  celery_beat (60s health)
                                                    └──────┬───────┘
                                       ┌──────────────┬───┴──────────────┐
                                       ▼              ▼                  ▼
                                 ┌──────────┐   ┌────────────┐    ┌──────────────┐
                                 │  Redis   │   │  Supabase  │    │  LLM client  │
                                 │ (state,  │   │ (Postgres+ │    │ Moonshot(test)│
                                 │  locks,  │   │  Storage)  │    │ GPT-4o (prod) │
                                 │  queue)  │   │  RLS by    │    └──────────────┘
                                 └──────────┘   │  shop_id   │
                                                └────────────┘
                                                           │
                                                  ┌────────────────┐
                                                  │  openpyxl Excel │
                                                  │  → Supabase     │
                                                  │  Storage → signed URL │
                                                  └────────────────┘
```

## Major components / modules

| Module | Responsibility | Stage | Status | Doc |
|--------|----------------|-------|--------|-----|
| `core` | config, logging, security headers | 0/12 | 🟡 stub | `src/app/core/README.md` |
| `db` | Supabase + Redis clients | 1 | 🟡 not started | `src/app/db/README.md` |
| `llm` | provider-agnostic LLM client (Moonshot/OpenAI) | 0/4 | 🟡 stub | `src/app/llm/README.md` |
| `tenants` | shop, shopkeeper, suspension | 1 | 🟡 not started | `src/app/tenants/README.md` |
| `telegram_bot` | command router, auth (shopkeeper/owner) | 2 | 🟡 not started | `src/app/telegram_bot/README.md` |
| `whatsapp` | Twilio webhook, signature verify | 3 | 🟡 not started | `src/app/whatsapp/README.md` |
| `messaging` | 9-step pipeline, dedup, session locks | 3 | 🟡 not started | `src/app/messaging/README.md` |
| `ai` | anti-hallucination, spec search, promotion | 4 | 🟡 not started | `src/app/ai/README.md` |
| `products` | inventory, addproduct, boost/tags | 5 | 🟡 not started | `src/app/products/README.md` |
| `orders` | order model, profit, status history | 8 | 🟡 not started | `src/app/orders/README.md` |
| `escalations` | pending, reply, handover, context freeze | 6 | 🟡 not started | `src/app/escalations/README.md` |
| `security` | 6 attack patterns, quarantine, incidents | 7 | 🟡 not started | `src/app/security/README.md` |
| `reports` | profit reports, dashboards, health | 8/10 | 🟡 not started | `src/app/reports/README.md` |
| `tasks` | celery app, tasks, beat schedule | 10 | 🟡 not started | `src/app/tasks/README.md` |
| `utils` | money, time, excel builder, storage | 9 | 🟡 not started | `src/app/utils/README.md` |

Status: 🟡 not started · 🔵 in progress · 🟢 done · 🔴 blocked

## Data flow (key paths)

**Customer WhatsApp message → AI reply:**
Twilio webhook → validate sig → shop lookup by `To` → check `shops.status` → blacklist → quarantine → bypass → attack-detect → enqueue Celery → acquire `lock:session:{shop_id}:{phone}` → dedup `MessageSid` → LLM (function-calling, `search_products`) → reply via Twilio. (SPEC §9)

**Escalation flow:**
LLM detects out-of-domain → write `pending_escalations` → notify shopkeeper Telegram → tell customer "connecting specialist" → freeze AI (subsequent msgs → shopkeeper) → shopkeeper `/reply {phone} {text}` → WhatsApp → `/handover {phone}` → AI resumes with Redis context. (SPEC §3)

## Deployment topology

- Single Docker Compose: `api`, `celery_worker`, `celery_beat`, `redis`, `flower` (SPEC §14).
- One deployment serves all 30 shops (SPEC §SYSTEM OVERVIEW).
- Supabase is hosted (SaaS Postgres + Storage); backend uses service-role key (RLS bypass) — see ADR-003.

## Cross-cutting concerns

- **Auth/identity:** owner = single Telegram ID whitelist; shopkeepers by `telegram_id`→`shop_id`; internal API = `X-Internal-API-Key` (SPEC §1).
- **Logging/observability:** structured logging; `/health`; Flower; Celery beat 60s health → critical Telegram alert (SPEC §13).
- **Config/secrets:** env-driven via pydantic-settings; `.env.example` is the manifest (ADR-001).
- **Error handling:** LLM errors retry once then fallback message (SPEC §11); never silently swallow (CONVENTIONS).
- **Audit:** `audit_logs` for owner/sensitive actions (SPEC §15, §16).
- **i18n/l10n:** AED currency; English UI strings (multi-currency deferred — see open questions).

## Architectural principles

1. **Tenant isolation is sacred.** Every query scoped by `shop_id`; RLS enforces it at the DB (ADR-003). No cross-shop DB access.
2. **Webhook returns 200 immediately**; all real work in Celery (SPEC §11).
3. **All state in Redis, zero local memory** (SPEC §11) — horizontal-safe.
4. **LLM is provider-agnostic** — one client, switch Moonshot↔OpenAI by env (ADR-004).
5. **AI never knows products from memory** — function-calling is the only product source (SPEC §3).
6. **Money is never float** — DECIMAL in DB, integer minor units in code (CONVENTIONS).

## Referenced ADRs

- `ADR-001` stack · `ADR-002` Telegram-first · `ADR-003` Supabase RLS · `ADR-004` LLM provider abstraction.
