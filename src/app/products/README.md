# Module: products

## Responsibility
Product inventory: `/addproduct` 11-step flow, image/video upload to Supabase Storage, boost/tags/feature commands, `productstats`. Owns `products` table queries.

## Boundaries
- **Owns:** `products` CRUD, `specs`/`boost_level`/`tags`/`is_featured` mutations, media upload.
- **Exposes:** `add_product`, `search_products(requirements, shop_id)` (called by `ai/`), boost/tag/feature ops, `productstats`.
- **Does NOT touch:** LLM; the `search_products` *tool declaration* is in `llm/functions.py`, execution here.

## Key files
| Path | Role | Stage |
|------|------|-------|
| `models.py` | `Product` (money = `Decimal`, never float) | 4 ✅ |
| `search.py` | ranking + tenant-scoped fetch (§4) | 4 ✅ |
| `service.py` | tenant guard + `create_product` + field parsers + boost/tag/feature (§4, §5) | 5 ✅ |
| `addproduct_flow.py` | 11-step `ConversationHandler` flow (§4) | 5 ✅ |
| `media.py` | Supabase Storage upload → `{shop_id}/{product_id}/…` | 5 ✅ |

## Tenant guard (the security control — do not weaken)
`product_id` arrives as shopkeeper free text, so it may name **another shop's** product. Every mutation in `service.py` resolves through `get_product(shop_id, product_id)` first, and each `_update` re-filters `.eq("shop_id", …)` at the DB layer too (RLS is still a permissive scaffold — the app layer is what's actually enforcing isolation today).

Cross-shop reads and writes both raise `ProductNotFound`, carrying the **same message as an unknown id** — never confirm that another shop's product exists. Tested: `tests/products/test_service.py::test_shop_b_cannot_mutate_shop_a_product`.

Supabase-py is **sync**: every DB call is wrapped in `asyncio.to_thread`. Calling `.execute()` straight from an `async def` blocks the event loop and stalls all bots (this bug shipped in Stage 4 and was fixed in Stage 5).

## Tag vocabulary (SPEC §5)
`parse_tags` enforces the 11-tag whitelist in `service.VALID_TAGS`. A typo (`clearence`) would be stored happily and then silently never promote, because the AI's promotion prompt only understands the real names.

## Ranking (SPEC §4) — `search.py`
```
relevance = matching spec pairs + (matching tags x 2)
score     = relevance * (1 + boost_level / 10)
order     = score DESC, is_featured DESC, boost_level DESC
```
The tail keys give SPEC §5 "vague request → featured first" for free: nothing matches → every score is 0 → featured surfaces. `rank()` is pure and fully tested; `search_products()` adds the DB fetch (`shop_id`-scoped, `quantity > 0`).

## Price ordering (Q-015, ADR-008 rev. 2) — do not weaken
`sort="price_asc"|"price_desc"` orders strictly by price and **ignores `boost_level` entirely**.

This is not an oversight. A relevance-ranked top-N slice cannot answer "cheapest" — and boost, whose whole job is to promote, actively hid a 2,499 AED phone behind a boosted 2,899 AED one. The model then stated it as fact. **Boost promotes; it must never lie.** Regression: `tests/products/test_search.py::test_cheapest_ignores_boost_the_q015_regression`.

A price sort still narrows to rows matching `requirements` first, so *"cheapest Samsung"* never returns a cheaper Apple. Only when nothing matches does it order the whole in-stock catalogue.

`_SYNONYMS` maps customer vocabulary onto the schema (`phone→mobile`, `cheap/cheapest→budget`, `deal→clearance`, `notebook→laptop`, …). Without it "phone" matches no `Mobile` row and "cheap" matches no `budget` tag, so those queries score 0 relevance everywhere and collapse to boost order. Extend the list as real customer language arrives.

**Two SPEC ambiguities are implemented, not decided — see Q-012 / Q-013 in `docs/12-OPEN-QUESTIONS.md`.** SPEC §4 states boost as *both* a hard sort key and a relevance multiplier (multiplier implemented), and literally excludes brand/model from search (included anyway — "Samsung phone" must match). Both are one-line changes. Don't silently re-decide.

## `/addproduct` flow
PTB `ConversationHandler`, 11 states (SPEC §4). `/cancel` at any step. Invalid input **re-asks** rather than advancing. Media is collected as bytes during the flow and uploaded only on `/save`.

The product id is generated **client-side** (`new_product_id()`) so media can be uploaded to `{shop_id}/{product_id}/` *before* the insert — one write, no update-after-insert.

`ponytail:` draft state lives in `context.user_data` (process memory), brushing SPEC §11's "all state in Redis". Fine for one bot process; a restart drops in-flight drafts. Upgrade path: PTB persistence backed by Redis.

## Storage
Bucket `shop-media` — **private**, created by `migrations/002_storage_buckets.sql` (it did not exist; the setting pointed at nothing). Backend uploads with the service-role key. `signed_urls(paths)` mints 1h download URLs so the customer channel can show a product's photos/video (the AI's `show_product_media` tool → `ai/orchestrator` → these URLs → the channel adapter sends them). Added in the Stage-12 live walkthrough (was deferred to Stage 13; brought forward because the Telegram test channel shows media too).

## Status
🟢 Stage 5 ✅ — `/addproduct` + media + boost/tag/feature all live on the keeper bot, verified end-to-end against the live Supabase project (create → upload → cross-shop denial → cleanup). `/productstats` deferred to Stage 8 (**Q-014** — no views/suggestions data exists). Spec ref: §4, §5.
