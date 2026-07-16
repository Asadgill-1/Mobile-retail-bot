# 12 — Open Questions

> Unresolved decisions. **An LLM must NOT guess on items listed here** — escalate them.
> When resolved, move the item to `04-TECH-DECISIONS.md` (with an ADR) and delete from here.

## Format

```
### Q-XXX — <question>
- Context: <why it matters>
- Options considered: <a / b / c>
- Decision needed from: <human role>
- Status: open | resolved (→ ADR-YYY)
- Blocking: <yes/no — what it blocks>
```

---

### Q-001 — What is the tech stack?  ✅ resolved
- → ADR-001.

### Q-002 — What exactly is the system? (domain)  ✅ resolved
- → `docs/SPEC-source.md` (multi-shop chatbot, 30 shops).

### Q-003 — Supabase project region / ownership?  ✅ resolved
- Context: RLS, latency, data residency (AED/UAE market suggests a nearby region).
- Resolution (2026-07-07): owner supplied a real Supabase project — ref `uwlczgwlkqlflpveeykj` (URL `https://uwlczgwlkqlflpveeykj.supabase.co`). Service-role + anon JWTs retrieved via Management API; account-level PAT (`sbp_…`) held for migrations. All wired into gitignored `.env`. REST verified 200 with the service-role JWT.
- Remaining: apply `migrations/001_init.sql` to the live project (DDL on real DB — confirm before pushing). Region not yet confirmed but no longer blocking.
- Status: resolved (creds). Migration apply pending owner go-ahead.

### Q-004 — Twilio number provisioning timeline?
- Context: One Twilio number per shop (30 numbers); all forward to a single webhook (SPEC §1). WhatsApp-enabled numbers take time to provision + Twilio approval.
- Options: provision now / provision at Stage 13.
- Decision needed from: owner.
- Status: open.
- Blocking: Stage 13 (deploy). Testing uses Telegram-first (ADR-002), so not blocking earlier stages.

### Q-005 — Moonshot vs OpenAI API key ownership?  ✅ resolved
- Context: Testing uses Moonshot (ADR-004); keys must be provisioned; who owns/bills each?
- Resolution (2026-07-07): owner supplied an **OpenRouter** API key (OpenAI-compatible aggregator) which routes to `moonshotai/kimi-k2` — honoring "use Moonshot for testing" via a single OpenAI-compatible client. Wired into `.env` (`AI_PROVIDER=openrouter`). Verified: a real chat completion returns 200 + a completion. ADR-004 revised.
- Status: resolved. `LLMClient.chat()` itself is still Stage 4 (function-calling + anti-hallucination), but the key is live and `is_configured=True`.

### Q-006 — Rider / delivery model details?  ✅ resolved (Stage 12b)
- Context: SPEC mentions `delivery_persons`, rider name/phone in Excel export (`/exportrider <rider_id>`), but the data model is thin (no zones, shift, capacity, geo). No flow ever assigned a rider to an order, so `/exportrider` shipped testable-but-empty.
- Resolution (2026-07-12, owner decided): **minimal model, owner-onboarded.** Owner runs `/addrider <shop> <phone> <name>`; a shop may have more than one rider. Rider links their own Telegram to one global rider bot by sharing their contact (phone-matched to their `delivery_persons` row(s) — migration 007 adds `telegram_id`). No zones/shift/vehicle — matches the "minimal now" option; extend later if the business needs it.
- Built alongside: a **custody handshake** (rider must `/accept`/`/notreceived` a handover before `/deliver`, answer written once — an owner-specified audit mechanism so neither shop nor rider can dispute who had the product) and **COD tracking** (migration 008: `orders.cod_amount`/`cash_received`/`custody`/`delivered_at`; append-only `cod_ledger`; keeper `/reconcilecod` reconciles end-of-day cash by the owner's formula: previous balance + today's COD − handed over = remaining).
- Full detail: `docs/07-CURRENT-STATE.md` Stage 12b, `src/app/riders/README.md`, `docs/10-DATA-MODEL.md`.
- Status: **resolved.** `/exportrider` now produces real data — `orders.rider_id` is set by `/assigndelivery`.

### Q-007 — Currency: hardcode AED or support multi-currency?
- Context: All reports/examples use AED (SPEC §6). Multi-currency adds a `currency` column + FX.
- Options: AED-only (default) / multi-currency.
- Decision needed from: owner.
- Status: open (defaulting to AED-only).
- Blocking: Stage 8 (profit reports). Not blocking earlier stages.

### Q-008 — Migration tooling?
- Context: Currently raw SQL in `migrations/`. Should we adopt alembic / Supabase migrations CLI for ordering & rollback?
- Options: raw SQL / alembic / Supabase CLI.
- Decision needed from: owner/lead.
- Status: open.
- Blocking: nothing urgent; revisit before Stage 12 (prod hardening).

### Q-009 — Testing bot/account layout?  ✅ resolved
- → ADR-005 (3 bots + 2 Telegram user accounts + Telethon userbot).

### Q-010 — Is there a "client company" layer above shops?
- Context: Clarified that the owner runs an **automation service company** and shops are **clients**. Currently `shops` = tenant (1:1 with client). But one client business might run **multiple shops** (e.g., a brand with 3 locations) and want consolidated reporting/management under one client account.
- Options: (a) keep `shop` = `client` 1:1 (current; simplest, matches spec); (b) add a `clients` table above `shops` (one client → many shops) for grouping/consolidated owner reports.
- Decision needed from: owner.
- Status: open (defaulting to 1:1 — option (a) — until Stage 8 owner reporting).
- Blocking: nothing immediate; revisit before Stage 8 (owner `/owner profit all`, `/owner profit compare`) if multi-shop clients exist.

### Q-011 — Per-client billing / SLA tracking in scope?
- Context: As a service company, you may want to track per-client usage, billing, SLAs, onboarding/offboarding dates.
- Options: (a) out of scope — handle billing outside the system (default, matches spec); (b) add `client_subscriptions`/usage tables later.
- Decision needed from: owner.
- Status: open (defaulting to out-of-scope for MVP).
- Blocking: nothing.

### Q-012 — SPEC §4 ranking: is boost a hard sort key or a relevance multiplier?
- Context: SPEC §4 states **both** "Sorts by: boost_level DESC, relevance score …, is_featured DESC" **and** "Relevance multiplied by (1 + boost_level/10)". These conflict: if `boost_level` is the primary sort key, the multiplier can never change an ordering, and any boosted product outranks a perfectly-matching unboosted one.
- Options: (a) **multiplier** — `score = relevance × (1 + boost/10)`, order by `(score, is_featured, boost_level)` DESC (implemented in `products/search.py`; boost strongly influences but cannot beat a much better match); (b) **hard key** — order by `(boost_level, score, is_featured)` DESC (boost always wins; relevance only breaks ties within a boost level).
- Implemented: (a). It also satisfies SPEC §5 "vague requests: prioritize featured" for free — when nothing matches, all scores are 0 and the tail keys surface featured items first. Covered by `tests/products/test_search.py::test_relevance_beats_boost_when_boost_cannot_close_the_gap`.
- Decision needed from: owner. (a) sells the right product; (b) sells the product you want to move.
- Status: open — **(a) implemented as the default**, one-line change to switch.
- Blocking: nothing. Revisit before go-live.

### Q-017 — How does an order get placed?  ✅ resolved (→ ADR-010)
- Resolution (2026-07-10, owner chose **hybrid A+B**): AI drafts, shopkeeper confirms. Assistant collects the order and calls `place_order` → a `draft` + shop notice; `/confirmorder` (atomic stock decrement + customer told the order number) or `/rejectorder`. Inventory checked at draft AND atomically at confirm; bargaining within a private per-product `min_price` floor; no premature customer message; draft doesn't freeze the AI. Full detail + rules in **ADR-010**. Live-verified: draft → confirm → stock decrement → cleanup.
- Still open downstream: order-placement flow now exists, but **multi-item orders** (schema is one product per order) and **status beyond confirmed** (packed/shipped/delivered) are not built — revisit with delivery (Stage 9+).

### Q-016 — Should the shop proactively disclose that customers are talking to an AI?
- Context: ADR-009 settled the *failure* case (customer never sees an error) and the *asked-directly* case (hand to a human — never deny, never confirm). It did **not** settle whether the shop should disclose automation up front, e.g. a first-message notice.
- Why it matters: bot-disclosure rules exist and differ by jurisdiction. California's **B.O.T. Act** (BPC §17940 et seq.) requires disclosure where a bot incentivises a commercial transaction. The **EU AI Act Art. 50** requires people be informed they are interacting with an AI system unless it is obvious from context. The owner sells into the **UAE** (AED pricing, SPEC §6), which has no equivalent statute today — but the codebase is generic and WhatsApp is global, so a customer roaming into a covered jurisdiction is plausible.
- What is already true: the assistant never claims to be human, never denies being a machine, and hands off to a person the moment anyone sincerely asks (ADR-009, verified live). Nothing in the system lies.
- Options: (a) status quo — no proactive disclosure, handoff when asked (current); (b) one-line notice on the first message of a conversation, e.g. "You're chatting with {shop}'s assistant — a real person can join any time."; (c) per-shop toggle on the `shops` row, so a client selling into the EU/California can switch it on.
- Recommendation: (a) now, (c) before any non-UAE client is onboarded. (b) costs a little conversion and buys legal cover; that's the owner's trade, not the engineer's.
- Decision needed from: owner (and, before EU/US onboarding, a lawyer — this is not legal advice).
- Status: open. Not blocking.

### Q-015 — `search_products` was blind to price and to category synonyms → wrong "cheapest" answers  ✅ resolved
- Context: found by running the real AI against the seeded test catalogue (`scripts/seed_test_catalog.py`). Customer: *"what's your cheapest phone?"* → AI: *"the Refurbished S23 Ultra at 2,899 AED"*. The actual cheapest was a **Galaxy S23 at 2,499 AED**, **not in the top-5 the AI received**.
- **The model hallucinated nothing.** It called the tool as instructed and truthfully reported the cheapest of the five rows it was handed. The tool lied by omission: it returned a boost-ranked, truncated slice. Boost — whose job is to promote — had hidden a cheaper product. *Grounding a model in a tool makes it exactly as truthful as the tool.*
- Two compounding causes: (1) price was never a sort key or filter, and the schema exposed only `requirements: string`; (2) "phone" matched nothing (`category` is `Mobile`) and "cheap" matched nothing (the tag is `budget`), so such queries scored 0 relevance everywhere and collapsed to boost order.
- Resolution (2026-07-08, **ADR-008 Revision 2**): `search_products` gained `sort: relevance|price_asc|price_desc` + `max_price_aed`. **Price ordering ignores `boost_level` entirely** — a promoted product must never hide a cheaper one. Price sorts still respect the customer's described requirements (so "cheapest Samsung" never returns a cheaper Apple). The system prompt gained a **superlative rule** (may not say cheapest/most expensive/"the only"/"nothing under X" without running the matching sorted search), the tool *description* states that a default search cannot answer superlatives, and `_SYNONYMS` maps customer words onto the schema. The orchestrator validates `sort` and falls back to `relevance` (models improvise enum values).
- Verified live on `moonshotai/kimi-k2`: "cheapest phone" → 2,499 AED Galaxy S23; "anything under 2600" → offers the 2,499 and nothing above; "most expensive" → 4,699 AED S23 Ultra 512GB. Regression tests: `tests/products/test_search.py::test_cheapest_ignores_boost_the_q015_regression` and `::test_price_sort_still_respects_what_the_customer_asked_for`.
- Status: **resolved → ADR-008 rev. 2.** Residual: superlative correctness depends on the model choosing `sort` — re-verify when switching provider (Stage 12 → GPT-4o). `_SYNONYMS` is hand-written and will need extending as real customer language arrives.

### Q-014 — `/productstats`: what are "views" and "suggestions", and where are they stored?  ✅ resolved (Stage 12d, 2026-07-16)
- Context: SPEC §5 defines `/productstats <product_id>` as "Views, suggestions, orders, profit generated". **None of that data exists.** There is no views/suggestions/impressions table in `001_init.sql`; `orders` exists but order + profit logic is Stage 8. In a chat interface there is also no natural "view" event — the closest thing is "the AI returned this product from `search_products`".
- Resolution: **views/suggestions were never built — orders/profit were, and that's what shipped.** `/productstats [period]` (`orders.service.product_stats` + `_fold_product_stats`) folds confirmed/delivered orders in the period (same status exclusions as `profit_summary`, so the two reports can never disagree) against the full catalogue, one row per product: sold qty, revenue, profit, current stock. Unsold products list with zeros at the bottom — dead stock, not view/suggestion counts, is what a shopkeeper actually needs from this report. `reports.service.format_product_stats` renders it; keeper `/productstats` and a new 📊 button (`keeper_stats_menu`, period-scoped) both call it.
- Live-verified: a live `/productstats` total for a shop/period **exactly matched** the live `/profit` total for the same window — the whole point of sharing `profit_summary`'s exclusions.
- Views/suggestions metering (original option (b)/(c)) was **not built** — nothing suggested it was needed once orders/profit existed. Revisit only if a shopkeeper explicitly asks for it.
- Status: **resolved.** See `docs/07-CURRENT-STATE.md` Stage 12d.

### Q-013 — SPEC §4 search: are brand/model/category searchable, or only `specs` + `tags`?
- Context: SPEC §4 says the backend "builds PostgreSQL query searching `specs` JSONB with ILIKE, also searches `tags`" — literally excluding `brand`, `model`, `category`, `color`, `condition`. But a customer typing "Samsung phone" is searching brand and category, and would match nothing.
- Options: (a) include brand/model/category/color/condition in the searchable text (implemented — `products/search.py::_spec_text`); (b) literal spec: `specs` + `tags` only.
- Implemented: (a), as the evident intent. Covered by `tests/products/test_search.py::test_brand_is_searchable`.
- Decision needed from: owner (confirm).
- Status: open — (a) implemented.
- Blocking: nothing.
