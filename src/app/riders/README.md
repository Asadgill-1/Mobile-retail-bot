# Module: riders

## Responsibility
Delivery rider onboarding, Telegram linking, custody handshake, delivery + COD (cash-on-delivery)
ledger. Owns `delivery_persons` writes and `cod_ledger`.

## Boundaries
- **Owns:** rider onboarding (`add_rider`), Telegram linking (`link_telegram`, phone-matched),
  custody transitions (`set_custody`), delivery finalization (`deliver_order`), cancellation
  (`cancel_delivery`), the COD ledger (`cod_rows`, `cod_balance`, `reconcile_cod`).
- **Exposes:** `add_rider`, `list_riders`, `get_rider`, `link_telegram`, `riders_by_telegram`,
  `custody_transition`, `deliverable`, `parse_cash`, `report_window`, `cod_trail` (pure rules);
  `set_custody`, `deliver_order`, `cancel_delivery`, `my_deliveries`, `delivered_report`,
  `cod_balance`, `reconcile_cod` (DB-backed flows).
- **Does NOT touch:** rider *assignment* to an order — that writes `orders.rider_id`/`cod_amount`
  and lives in `orders.service.assign_delivery` (orders owns its own writes; riders is the guest).
  Order status transitions before delivery (`confirmed→packed→shipped`) stay in `orders.service`.

## Dependencies
- **Depends on:** `orders.service` (`_decrement_stock`, `_set_status` — shared writers, not
  duplicated), `reports.service` (`DUBAI`, `parse_period` — the UAE day boundary), `db/factory`
  (shop lookup), `telegram_bot/notify` (`send_to_rider`, `send_to_customer`, `send_to_shopkeepers`).
- **Depended on by:** `orders.service.assign_delivery` (COD amount + balance on the assignment
  card), `telegram_bot/bot.py` (the rider bot + keeper `/reconcilecod`).

## Key files
| Path | Role |
|------|------|
| `service.py` | onboarding, linking, custody, delivery, cancel, COD ledger — all of it (one file, mirrors `orders/service.py`'s size) |

## Data / state
`delivery_persons` (+ `telegram_id`, migration 007) and `cod_ledger` (migration 008). See
`10-DATA-MODEL.md`. `orders` gained `cod_amount`, `cash_received`, `delivered_at`, `custody`,
`custody_at`, `cancel_remarks` (migration 008) — owned by `orders`, written by both modules.

## Behavior notes — the custody handshake (audit: who has the product)

Assignment sets `custody='offered'`. The rider must answer before they can deliver:
- `/accept` → `'accepted'` — "yes, I have this product." Shop notified.
- `/notreceived` → `'disputed'` — "this was NOT handed to me." Shop alerted immediately.

**The answer is written once** (`custody_transition` raises `ValueError` on a second call) — neither
side can later dispute the handover. `deliverable(status, custody)` refuses `/deliver` until custody
is `'accepted'`; a disputed or still-offered order cannot be marked delivered.

## Behavior notes — COD money (append-only ledger, never a mutable balance)

`cod_ledger` has two entry types: `'collect'` (written by `deliver_order`, one row per delivery) and
`'handover'` (written by `reconcile_cod`, one row per end-of-day cash-in). **Balance is always
`Σcollect − Σhandover`**, re-derived from the full row set — there is no counter column to drift out
of sync. `cod_trail(rows, today_start)` folds this into the reconcile numbers the owner specified:
*previous balance (everything before today) + today's collections − today's handover = remaining*.
The same trail text is pushed to the rider so both sides hold the identical record.

## Behavior notes — everything attaches to the order

`orders.rider_id`, `cod_amount`, `cash_received`, `delivered_at`, `custody`/`custody_at`, and
`cancel_remarks` all live on the `orders` row itself (not a side table) — one order number is the
whole audit trail. `cod_ledger` rows link back via `order_id` (null on `handover` rows, which aren't
tied to one delivery). `order_status_history` logs `changed_by='rider'` for deliver/cancel, same
table Stage 8 already used for `system`/`shopkeeper`.

## Tests
- Location: `tests/riders/test_service.py`
- Command: `PYTHONPATH="src;config;." pytest tests/riders -q`
- Also: `python src/app/riders/service.py` — pure-logic `__main__` self-check (phone normalize,
  custody, deliverable, cash parse, COD trail math — no DB).

## Status
🟢 done. Live-verified end-to-end (assign→accept→deliver→report→reconcile) against real Supabase +
real Telegram on Shop 01 order #7. 26 tests (`tests/riders/`; assignment-side COD tests live in
`tests/orders/test_service.py`).

## Open issues
- none. (Q-006 rider model — resolved; see `12-OPEN-QUESTIONS.md`.)
