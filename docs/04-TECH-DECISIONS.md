# 04 — Tech Decisions (Decision Log Index)

> Authoritative list of technology & architecture choices. **If it's not here, it's not decided.**
> Each entry links to a full ADR in `docs/DECISIONS/`.

## Format

`ADR-XXX · short title · status · date`

Status: `Proposed` · `Accepted` · `Superseded by ADR-YYY` · `Deprecated`

## Decisions

| ID | Title | Status | Date | File |
|----|-------|--------|------|------|
| ADR-001 | Lock tech stack (Python/FastAPI/Supabase/Redis/Celery/…) | Accepted | 2026-07-07 | `DECISIONS/ADR-001-tech-stack.md` |
| ADR-002 | Telegram-first testing; WhatsApp at pre-deploy | Accepted | 2026-07-07 | `DECISIONS/ADR-002-telegram-first-testing.md` |
| ADR-003 | Supabase RLS for tenant isolation | Accepted | 2026-07-07 | `DECISIONS/ADR-003-supabase-rls-isolation.md` |
| ADR-004 | LLM provider abstraction — **official Moonshot `kimi-k2.6` direct** (test) / OpenAI GPT-4o (prod) | Accepted (rev.2) | 2026-07-08 | `DECISIONS/ADR-004-llm-provider-abstraction.md` |
| ADR-005 | Testing topology — 5 bots (owner + per-shop keeper + customer) + 2 Telegram accounts + Telethon userbot | Accepted (revised) | 2026-07-07 | `DECISIONS/ADR-005-testing-topology.md` |
| ADR-006 | Client layer above shops + per-client usage tracking | Accepted | 2026-07-07 | `DECISIONS/ADR-006-clients-and-usage-tracking.md` |
| ADR-007 | Supabase MCP server for DB ops (migrations + SQL via Management API) | Accepted | 2026-07-07 | `DECISIONS/ADR-007-supabase-mcp.md` |
| ADR-008 | AI tool surface — 2 tools, escalation by tool-call, `boost_level` withheld from the model | Accepted (rev.2: price ordering) | 2026-07-08 | `DECISIONS/ADR-008-ai-tool-surface.md` |
| ADR-009 | Failure handling & AI disclosure — customer never sees an error; only the owner is paged | Accepted | 2026-07-08 | `DECISIONS/ADR-009-failure-handling-and-ai-disclosure.md` |
| ADR-010 | Hybrid order booking — AI drafts, shopkeeper confirms; atomic stock decrement; secret bargaining floor | Accepted | 2026-07-10 | `DECISIONS/ADR-010-hybrid-order-booking.md` |

## Quick reference (decided)

- **Language/runtime:** Python 3.11+
- **Web framework:** FastAPI (async)
- **DB / storage:** Supabase (Postgres + Storage), RLS by `shop_id`
- **Cache / queue state:** Redis
- **Task queue:** Celery + Celery Beat
- **Messaging:** Twilio (WhatsApp), python-telegram-bot v20+ (Telegram)
- **LLM:** official Moonshot `kimi-k2.6` (direct, `api.moonshot.ai`) for testing; OpenAI GPT-4o for prod — via OpenAI-compatible client. `AI_TEMPERATURE=1.0` is mandatory for `kimi-k2.*`
- **Excel:** openpyxl
- **Deploy:** Docker Compose (api, celery_worker, celery_beat, redis, flower)

## How to add a decision

1. Copy `docs/TEMPLATES/ADR-TEMPLATE.md` → `docs/DECISIONS/ADR-XXX-<slug>.md`.
2. Fill it in.
3. Add a row to the table above.
4. Update `docs/07-CURRENT-STATE.md`.
