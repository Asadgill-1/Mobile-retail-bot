# Module: whatsapp

## Responsibility
Twilio/WhatsApp inbound + outbound: webhook receiver, signature validation, `To`→shop lookup, sending replies. Mocked during testing (ADR-002); activated at Stage 13.

## Boundaries
- **Owns:** Twilio HTTP client, signature verification, message send.
- **Exposes:** `verify_signature`, `resolve_shop_from_to`, `send_whatsapp(shop_id, phone, text)`.
- **Does NOT touch:** message processing logic (delegates to `messaging/`).

## Key files
| Path | Role | Stage |
|------|------|-------|
| `webhook.py` | FastAPI route + signature verify + 200-immediate | 3 |
| `twilio_client.py` | send + mockable interface | 3 |
| `mock.py` | in-test mock sender | 3 |

## Status
🟢 Stage 3 ✅ — `webhook.py`: `POST /webhook/whatsapp` with real Twilio signature verify + `To`→shop lookup + enqueue + 200-immediate (SPEC §11). Mounted in `main.py`. **Mocked/dormant until Stage 13** (no real numbers); outbound `twilio_client.py`/`mock.py` deferred to Stage 13 (nothing sends yet). Sig-verify is unit-tested now so Stage 13 is a cutover. Spec ref: §1, §9 step 1.
