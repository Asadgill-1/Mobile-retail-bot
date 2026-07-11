# ADR-010 — Hybrid order booking: AI drafts, shopkeeper confirms

**Status:** accepted (2026-07-10)
**Context:** Q-017. SPEC never specified how an order gets placed. Stage 8 built the profit engine and Stage 9 builds Excel export — both read `orders`, which nothing wrote except `create_order()` in tests. A booking mechanism was needed.

## Decision

Orders are **AI-drafted, human-confirmed**. The assistant collects the order in chat and calls a `place_order` tool → a `draft` order + a Telegram notice to the shop's staff. A shopkeeper `/confirmorder <#>` (atomic stock decrement + customer told the order number) or `/rejectorder <#>`. The customer never sees the machine and is told nothing until the shop confirms.

### Why hybrid, not pure-AI or pure-manual
- **The human gate is on the money/inventory write.** A mis-parsed address or quantity is cheap to catch before the order is real, expensive after. A person confirms the actual write (the "never simplify away the money path" rule).
- **The AI removes the data-entry tedium** that pure-manual `/addorder` would impose on staff for every sale.

## Key rules

1. **No premature customer message.** The AI gathers everything, submits the draft silently, and replies briefly and naturally — no "let me confirm that". The customer learns the order (number + summary) only on `/confirmorder`. On reject, the customer is **not** cold-messaged; staff `/reply` if they choose. (Q-017 #2)
2. **Inventory checked twice.** At draft time (`quantity >= requested`, else no draft — the shop is never bothered). At confirm time an **atomic** `decrement_stock` RPC (`UPDATE … WHERE quantity >= n`, migration 003) — two racing confirms can't oversell. (Q-017 #1, #4)
3. **Draft does not freeze the AI.** Unlike an escalation, the customer keeps chatting with the assistant while a draft is pending. A new order intent supersedes the pending draft (cancels it). True message ordering under rapid-fire is the Stage 11 per-session lock. (Q-017 #3)
4. **Bargaining is human-in-the-loop** (see Revision 1 below). The AI has **no** discount authority.

## Revision 1 (2026-07-10) — negotiation is a live shop decision, not a secret floor

The original rule #4 let the AI auto-discount down to a private `min_price`. The owner rejected that ("I don't want the human as a rubber stamp — I want it in the loop"). Replaced with an explicit approval round-trip:

- **Per-shop toggle** `shops.negotiation_enabled` (default true), keeper `/negotiation on|off`. **Off = the AI never discounts** — checked fresh from the DB on every haggle (a just-toggled shop is respected immediately), so a stale bot snapshot can't leak a discount.
- **The loop:** customer haggles → AI calls `request_price(product, offer)` (never quotes a discount itself) → a `price_requests` row + a shop notice showing offer / list / **cost** / margin. Shopkeeper `/approveprice <#>`, `/custom <#> <price>` (counter), or `/denyprice <#>`. The **system** tells the customer the outcome. `draft_order` then applies the shop-**approved** price automatically; with no approval on record the price is list. The AI literally cannot invent a discount.
- `products.min_price` and the `place_order` `unit_price` argument are **removed**. `place_order` no longer carries a price at all.

## Schema
- migration 003: `orders.status` += `draft`; `orders.order_number` (identity serial); `decrement_stock()` RPC.
- migration 004: `shops.negotiation_enabled`; `price_requests` table (request_number serial, requested/approved price, status); **drops `products.min_price`**.

## Consequences
- `create_order()` gains a `status` param and returns the inserted row (for `order_number`). Profit excludes `draft`.
- Order/price notices to the shop show buy(cost) / list / discount / margin so staff decide with full context (owner request #2).
- `order_number` and `request_number` are global sequences, not per-shop (`ponytail:` — per-shop numbering only if a shop asks).
- **Not built:** multi-item orders (schema has one `product_id`); order status beyond confirmed (packed/shipped/delivered → Stage 9+ delivery). 
