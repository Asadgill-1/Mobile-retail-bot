# Module: utils

## Responsibility
Cross-cutting helpers: money (`Decimal`, AED), UTC time, the Excel pick-&-pack builder (SPEC §10), and Supabase Storage report upload + signed URLs.

## Boundaries
- **Owns:** pure helpers + the pure Excel builder + the report-storage helper.
- **Exposes:** `orders_workbook(rows, *, detailed)` (excel.py), `upload_report(shop_id, filename, data)` (storage.py); plus money/time helpers.
- **Does NOT touch:** business rules or DB queries — the order query lives in `orders.service` (`orders_for_export` / `rider_orders_for_export`), which passes rows in.

## Excel builder (§10)
`orders_workbook(rows, *, detailed=False) -> bytes` is **pure** (no I/O). It takes the DB order
shape used everywhere else — each row carries an embedded `products` dict (and `delivery_persons`
for rider sheets) — and flattens it into the §10 columns. White-bold-on-`#2563EB` header, thin
borders, auto-width (capped 50), frozen header row. `detailed` appends Order Time / Rider Name /
Rider Phone / Special Instructions. **RAM/Storage come from `products.specs`** (case-insensitive;
`storage` or `rom`). **Selling Price = `selling_price − discount_amount`** (what the customer pays).
Keeping the flatten here (not in the service) makes the whole mapping unit-testable by reloading
the bytes — see `tests/utils/test_excel.py` + the `__main__` self-check.

## Storage (§10)
`upload_report(shop_id, filename, data) -> signed_url` uploads to the **private** `shop-reports`
bucket under `{shop_id}/…` and returns a **24h** signed download URL. Separate from
`products/media.py` (product-scoped, never signs — nothing sends media until Stage 13). sync
supabase-py → `asyncio.to_thread`. Bucket created by `migrations/005_reports_bucket.sql`.

## Key files
| Path | Role | Stage |
|------|------|-------|
| `excel.py` | pure openpyxl builder (blue #2563EB header, borders, auto-width) (§10) | 9 ✅ |
| `storage.py` | `shop-reports` upload + 24h signed URL | 9 ✅ |
| `money.py` | Decimal money + AED formatting | 8 |
| `time.py` | UTC timestamps | 1 |

## Status
🟢 Excel export live (Stage 9), round-trip live-verified. Spec ref: §6, §10.
