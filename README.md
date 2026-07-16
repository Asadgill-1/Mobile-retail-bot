# Multi-Shop Chatbot

> **Status:** 🟢 Stages 0–12 core complete + full live QA audit + delivery/rider/COD + shop-owner bot + inline-button UX + gap-fix wave (Stages 12b–12d) · 487 tests passing · 7 bots live · next: Stage 13 (WhatsApp/Twilio cutover)
> **Spec:** [`docs/SPEC-source.md`](docs/SPEC-source.md) (immutable)
> **Current state:** [`docs/07-CURRENT-STATE.md`](docs/07-CURRENT-STATE.md)

A production-grade, multi-tenant chatbot **platform** for an automation service company: each **client shop** (mobile/computer store) is an isolated tenant. WhatsApp (customers) + Telegram (owner/shopkeepers/riders/shop-owners), AI anti-hallucination with function calling, intrusion detection + auto-quarantine, suspension/escalation/bypass, profit reporting (incl. counter/walk-in sales via vision extraction), friendly reference codes, and Excel order export — single deployment, complete tenant isolation.

- **Testing phase:** Telegram-first, LLM = **Moonshot (Kimi)**, WhatsApp mocked.
- **Production:** LLM = **OpenAI GPT-4o**, real Twilio/WhatsApp activated. (ADR-004, ADR-002)

## 🤖 For LLMs / AI agents (READ THIS FIRST)

1. [`AGENTS.md`](AGENTS.md) — handoff protocol & rules
2. [`docs/00-HOW-TO-USE.md`](docs/00-HOW-TO-USE.md) — how the context layer is organized
3. [`docs/07-CURRENT-STATE.md`](docs/07-CURRENT-STATE.md) — exactly where the project is now
4. [`docs/06-ROADMAP.md`](docs/06-ROADMAP.md) — the staged plan
5. [`docs/03-ARCHITECTURE.md`](docs/03-ARCHITECTURE.md) — how it fits together
6. [`docs/05-CONVENTIONS.md`](docs/05-CONVENTIONS.md) — code rules you MUST follow
7. The module doc under `src/app/<module>/README.md` for the stage you're on

**Never start coding without reading `CURRENT-STATE` and the relevant module doc.**

## Quick links

| File | Purpose |
|------|---------|
| `AGENTS.md` | Handoff protocol for AI assistants |
| `.context/HANDOFF.md` | Single-file resume brief |
| `docs/` | Full system documentation (01–12 + SPEC + ADRs + templates) |
| `migrations/001_init.sql` | Full DB schema + RLS + seed |
| `src/app/` | Source code (15 modules, each with a README) |
| `tests/` | Test suite |

## Run (dev)

```bash
cp .env.example .env   # fill in
pip install -r requirements.txt
./scripts/run_dev.sh   # uvicorn on :8000, long-polling Telegram, mocked WhatsApp
# or: docker compose up --build
```

## Stack

Python 3.11 · FastAPI · Supabase (Postgres + Storage, RLS) · Redis · Celery (+Beat/Flower) · python-telegram-bot v20 · Twilio · OpenAI-compatible LLM (Moonshot/OpenAI) · openpyxl · Docker Compose.

## License

TBD
