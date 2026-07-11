# ADR-001 — Lock tech stack

- **Status:** Accepted
- **Date:** 2026-07-07
- **Deciders:** owner
- **Stage when decided:** 0

## Context

The system (SPEC §SYSTEM OVERVIEW, §14) is a production-grade multi-tenant chatbot for 30 shops. It needs async I/O (webhooks + 300+ concurrent conversations), a relational DB with per-tenant isolation, a task queue for non-blocking processing, two messaging channels (WhatsApp + Telegram), an LLM with function calling, Excel export, and containerized deployment. The spec explicitly mandates the stack (§14).

## Options considered

### Option A — Spec stack (Python 3.11 / FastAPI / Supabase / Redis / Celery / python-telegram-bot / Twilio / openpyxl / Docker)
- Pros: matches spec exactly; async; mature; Supabase gives Postgres + Storage + RLS; Celery gives reliability.
- Cons: many moving parts; Supabase is a hosted dependency.

### Option B — Node/TypeScript + Postgres + BullMQ
- Pros: one language front+back; strong async.
- Cons: diverges from spec; loses Supabase RLS convenience; python-telegram-bot/Twilio/openpyxl ecosystem familiarity lost.

### Option C — Managed PaaS (no Docker)
- Pros: less ops.
- Cons: spec mandates Docker Compose; less control for production-grade hardening.

## Decision

Adopt **Option A** — the spec stack (§14): Python 3.11+, FastAPI, Supabase (Postgres + Storage), Redis, Celery (+ Beat), python-telegram-bot v20+, Twilio SDK, openpyxl, Docker Compose.

## Rationale

Spec-mandated; all requirements (async, isolation, queue, channels, LLM, Excel, prod Docker) are covered. Keeps the project aligned with the source spec and avoids re-litigating choices.

## Consequences

- Positive: single source of truth for deps in `requirements.txt`; clear module boundaries.
- Negative: Supabase hosting cost + region decision (Q-003); multiple services to operate.
- Follow-ups: pin versions; pick Supabase region (Q-003); Twilio provisioning (Q-004).

## Related

- ADR-002 (testing approach), ADR-003 (RLS), ADR-004 (LLM provider).
- SPEC §14.
