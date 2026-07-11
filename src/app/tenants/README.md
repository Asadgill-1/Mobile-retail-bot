# Module: tenants

## Responsibility
Tenant (shop) lifecycle: shop/shopkeeper models, suspension state, owner-auth helper. Foundation for all `shop_id`-scoped operations (SPEC §1, §2).

## Boundaries
- **Owns:** shop/shopkeeper/suspension logic + owner/shopkeeper auth.
- **Exposes:** `TenantService` (suspend/resume/status/is_suspended/get_shop_by_whatsapp_number), `is_owner`, `require_owner`, `resolve_shopkeeper`.
- **Does NOT touch:** message pipeline (`messaging/`); it only answers "is this shop active / who is this telegram user".

## Key files
| Path | Role | Stage |
|------|------|-------|
| `models.py` | Pydantic Shop, Shopkeeper, ShopStatus, ShopStatusInfo | 1 ✅ |
| `service.py` | `TenantService` — suspend/resume/status | 1 ✅ |
| `auth.py` | `is_owner`, `require_owner`, `resolve_shopkeeper` | 1 ✅ |

## Status
🟢 Stage 1 complete. 17 tests passing. Owner command wiring (Telegram) is Stage 2.
Spec ref: §1, §2.
