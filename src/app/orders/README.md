# Module: orders

## Responsibility
Order model, status history, profit calculation (SPEC §6 formula). Owns `orders`, `order_status_history`, `delivery_persons` queries.

## Boundaries
- **Owns:** order writes, hybrid booking (draft/confirm/reject), profit math + aggregation.
- **Exposes:** `create_order(...)`, `draft_order(...)`, `confirm_order(...)`, `reject_order(...)`, `list_drafts(...)`, `profit_summary(shop_id, start, end)`, `line_profit(...)`, `ProfitSummary`; export: `export_orders(...)`, `export_rider(...)`, `orders_for_export(...)`, `rider_orders_for_export(...)`.
- **Does NOT touch:** report formatting (in `reports/`); Excel styling + Storage (in `utils/excel.py` + `utils/storage.py`); the LLM tool schema (in `llm/`, executed by `ai/orchestrator`).

## Hybrid booking (Q-017 → ADR-010)
AI drafts, shopkeeper confirms. `draft_order` (from the `place_order` tool) checks stock, applies any
shop-approved price, writes a `draft`, and notifies staff (cost/list/discount/margin) — **no customer
message yet**. `/confirmorder` runs the atomic `decrement_stock` RPC (migration 003), marks `confirmed`,
and tells the customer the `order_number`. `/rejectorder` cancels without cold-messaging the customer.
A pending draft does not freeze the AI; a new order intent supersedes it.

## Price negotiation (ADR-010 rev.1 — human in the loop)
The AI has **no** discount authority. Customer haggles → `request_price` (from the tool) raises a
`price_requests` row + shop notice; shopkeeper `/approveprice`, `/custom <price>`, or `/denyprice`; the
system tells the customer. `draft_order` uses only a shop-**approved** price (`_approved_price`), else
list. Per-shop `set_negotiation` (`/negotiation on|off`) — the off check is read **fresh** from the DB
every haggle, so a shop that just turned it off gives no discount even before its bot restarts. The old
secret `min_price` floor was removed in migration 004.

## Excel export (SPEC §10 — Stage 9)
`orders_for_export(shop_id, filter)` (`today|yesterday|YYYY-MM-DD|pending|all`; **drafts never
exported**; `pending` = confirmed; reuses `reports.parse_period` for dates) and
`rider_orders_for_export(shop_id, rider_id, filter)` (one rider, **sorted by address**) fetch rows;
`export_orders` / `export_rider` hand them to `utils.excel.orders_workbook` → `utils.storage.upload_report`,
returning `(filename, 24h signed URL, row_count)`. Wired to keeper `/exportorders` / `/exportrider`.
`rider_id` is never assigned yet (Q-006) → rider sheets are empty until a rider-assignment flow lands.

## Profit formula (§6)
`line_profit = selling_price - discount_amount - cost_price × quantity`
`margin% = profit / cost × 100` (0 when no cost — no divide-by-zero on empty)

> **quantity matters.** SPEC's literal formula omits it, but `cost_price` is per-unit while
> `selling_price` is the line total — so cost is `cost_price × quantity`. Dropping it under-counts
> cost on any multi-unit order (a silent money bug). Pinned by `models.py` `__main__` + tests.

## Key files
| Path | Role | Stage |
|------|------|-------|
| `models.py` | `ProfitSummary` + pure `line_profit` (money path) | 8 ✅ |
| `service.py` | `create_order` (tenant-guarded) + `profit_summary` (range aggregation) | 8 ✅ |

## Status
🟢 profit live (Stage 8). `create_order` is the **only** writer of `orders`; it reuses
`products.service.get_product` as the tenant guard (product must belong to the shop). Aggregation
fetches the range + embeds `products(cost_price,tags)` and sums in Python (`ponytail:` — RPC if a
shop does thousands/day).

**Not built:** a customer/shopkeeper **order-placement flow** (SPEC never specs one — today orders
exist only via `create_order()` in tests/seeds), `update_status`, `/productstats` (Q-014, no view
data). Delivery model still under-specified (Q-006). Spec ref: §6, §15.
