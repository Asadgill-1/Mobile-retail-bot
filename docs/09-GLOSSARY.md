# 09 — Glossary

> Domain & project terms. Keeps every LLM using the same vocabulary.

| Term | Definition |
|------|-----------|
| **shop_id** | The tenant identifier. Every operational table has `shop_id` FK to `shops.id`; all queries scoped by it (SPEC §1). |
| **tenant** | A single client shop in this system; tenant isolation = no cross-client data leakage. |
| **client** | A service-provider client business that pays for the chatbot service. One client may own multiple shops (ADR-006). The `clients` table sits above `shops`. |
| **usage_daily** | Per-client/shop daily aggregate table (message counts, escalations, AI calls) for billing insight (ADR-006). Fed from Redis counters by a daily beat job. |
| **owner (service provider)** | **You** — the person who runs the automation service company. Global god-mode across all client shops; identified by `OWNER_TELEGRAM_ID` (SPEC §SYSTEM OVERVIEW). |
| **shopkeeper** | A **client's own staff** (the client's employees), scoped to their `shop_id`; authenticated by `telegram_id`→`shop_id` (SPEC §1). They manage their shop's products/orders/escalations, not the platform. |
| **escalation** | Out-of-domain customer request (refund, complaint, human, legal, etc.) routed to the shopkeeper (SPEC §3). |
| **handover** | Returning a frozen conversation from the shopkeeper back to the AI with full Redis context (SPEC §3, `/handover`). |
| **freeze (AI)** | State where the AI stops answering a customer; subsequent messages forward to the shopkeeper's Telegram (SPEC §3). |
| **quarantine** | Redis key `quarantine:{phone}` (1h TTL) auto-set on attack detection; customer gets generic reply (SPEC §7). |
| **bypass (direct-to-shop)** | `bypass_ai:{phone}` routes a customer's messages straight to the shopkeeper, skipping the AI (SPEC §8). |
| **boost_level** | Integer 0–10 on a product; raises its ranking in `search_products` and AI presentation order (SPEC §4, §5). |
| **tags** | TEXT[] on products (clearance, trending, best_camera, …) used in search relevance and AI promotion (SPEC §4, §5). |
| **is_featured** | Boolean flag; featured products prioritized for vague requests (SPEC §5). |
| **RLS** | Row-Level Security (Supabase/Postgres) — enforces `shop_id` scoping at the DB level (ADR-003). |
| **Moonshot** | Moonshot AI / Kimi — the LLM provider used during testing in place of OpenAI (ADR-004). |
| **GPT-4o** | OpenAI model used in production (ADR-004). |
| **function calling** | LLM feature used as the *only* source of product data (anti-hallucination) (SPEC §3, §4). |
| **search_products** | The LLM tool/function that queries products by natural-language requirements (SPEC §4). |
| **MessageSid** | Twilio message identifier; used for deduplication (5-min Redis TTL) (SPEC §11). |
| **session lock** | Redis key `lock:session:{shop_id}:{phone}` (30s timeout) preventing concurrent processing of one conversation (SPEC §11). |
| **audit_logs** | Table capturing owner/sensitive actions (SPEC §15, §16). |
| **userbot** | A Telegram *user account* driven programmatically via MTProto (Telethon). Used in tests because bots can't message bots (ADR-005). |
| **staff bot** | The single Telegram bot owner + shopkeepers use (production, SPEC §1). |
| **customer-facing bot** | A per-shop Telegram bot used only in testing to stand in for that shop's WhatsApp/Twilio number (ADR-005). |
| **security_incidents** | Table capturing attack forensics (last 25 messages) + incident ID (SPEC §7). |

## Acronyms

| Acronym | Expands to |
|---------|-----------|
| POS | (not used here — chatbot-only) |
| RLS | Row-Level Security |
| TTL | Time-To-Live (Redis key expiry) |
| FK | Foreign Key |
| AED | UAE Dirham (currency) |
| LLM | Large Language Model |
| SID | Twilio Account SID / MessageSid |
