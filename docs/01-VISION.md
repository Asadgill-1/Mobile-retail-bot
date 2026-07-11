# 01 — Vision

> Source: `docs/SPEC-source.md` §SYSTEM OVERVIEW.

## Problem

The owner runs an **automation service company**: shops (mobile/computer stores) are **clients** that pay for a chatbot/automation service, not branches of one retail chain. The owner (service provider) needs to serve each client's customers over chat (WhatsApp + Telegram) without dedicating a human to each conversation, while keeping **full control, security, and observability** across all client shops. Per the spec, shop staff do **not** self-manage the platform — the service provider operates everything on the clients' behalf. Today the pain is: missed messages, inconsistent answers, hallucinated product info, leaked cross-client data, no attack defense, and no per-client profit visibility.

## Solution (one sentence)

A single-deployment, multi-tenant (one tenant = one client shop) chatbot platform where one AI (function-calling, anti-hallucination) serves all client shops with strict `shop_id` isolation, service-provider-controlled suspension/escalation/security, and per-client profit + Excel reporting — tested on Telegram (LLM = Moonshot) and deployed on WhatsApp (LLM = GPT-4o).

## Goals (primary)

1. **Never drop a message, never leak data** across shops — full tenant isolation (SPEC §1, §11).
2. **AI never invents products/prices/specs** — function calling is the *only* source of product data (SPEC §3).
3. **Auto-defend against attacks** — 6 attack patterns detected + auto-quarantine + owner forensics (SPEC §7).
4. **Owner has full control & observability** — suspension, escalation, bypass, dashboards, profit, audit (SPEC §2, §6, §12, §13).
5. **300+ concurrent conversations** reliably (SPEC §SYSTEM OVERVIEW, §11).

## Non-goals (explicitly not doing)

- Shopkeeper self-management of shops / owner-only control preserved (SPEC §SYSTEM OVERVIEW).
- Non-WhatsApp / non-Telegram channels.
- Customer-facing admin UI (control is via Telegram commands).

## Success criteria

- [ ] 300+ concurrent conversations with zero dropped messages under load.
- [ ] No cross-shop data leakage (RLS + `shop_id` scoping enforced, tested).
- [ ] AI hallucination rate on product facts = 0 (every product fact sourced from `search_products`).
- [ ] All 6 attack patterns trigger quarantine + owner alert + forensic capture.
- [ ] Owner can suspend/resume any shop in <5s; suspended shop auto-replies and freezes AI.
- [ ] Profit reports match manual calc per formula (SPEC §6).

## Target users / personas

| Persona | What they need |
|---------|----------------|
| **Owner (service provider — you)** | Full control, security alerts, profit + dashboards across all client shops; onboards/suspends clients |
| **Shopkeeper (client's staff)** | Add/tag/boost products, receive escalations, reply/handover, export orders, view their shop's profit |
| **Customer (of a client shop)** | Ask about products in natural language, get accurate answers, escalate to human |

## Inspiration / references

- Original spec: `docs/SPEC-source.md` (verbatim).
