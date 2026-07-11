# 11 — API Contracts

> Source: `docs/SPEC-source.md` §1, §9, §13. Public/internal interfaces.

## Conventions

- Web framework: FastAPI (async).
- Internal endpoints require `X-Internal-API-Key` header (SPEC §1).
- Format: JSON (HTTP), form-encoded (Twilio webhooks).
- Errors: `{ "error": "<safe message>" }`; never leak internals.

## HTTP endpoints

### `POST /webhook/whatsapp` (Twilio)
- **Purpose:** Inbound WhatsApp message receiver. Single webhook for all shops.
- **Auth:** Twilio signature validation (SPEC §9 step 1).
- **Input:** Twilio form params (`MessageSid`, `From`, `To`, `Body`, `MediaUrl*`).
- **Behavior:** Return **200 immediately**; enqueue Celery task. Shop resolved from `To` number (SPEC §1).
- **Response:** 200 OK (empty body) — never block.

### `POST|GET /webhook/telegram` (or long-polling in testing)
- **Purpose:** Telegram bot updates (commands from owner/shopkeepers).
- **Auth:** Telegram webhook secret (TBD) + per-command auth (owner Telegram ID / shopkeeper telegram_id).
- **Testing mode:** long-polling (ADR-002).

### `GET /health`
- **Purpose:** System health (SPEC §13).
- **Auth:** `X-Internal-API-Key` (or basic-auth — finalize in Stage 10).
- **Response:**
  ```json
  { "db": "ok|fail", "redis": "ok|fail", "llm": "ok|fail",
    "twilio": "ok|fail|mocked", "celery_workers": N,
    "active_conversations": N, "quarantined_count": N }
  ```

### `/flower`
- **Purpose:** Celery monitoring UI (SPEC §13).
- **Auth:** HTTP basic-auth.

## Internal API (service-to-service)
- Header `X-Internal-API-Key` required on all non-webhook internal endpoints (SPEC §1).

### `GET /internal/usage/{client_id}?day=YYYY-MM-DD` (Stage 8, ADR-006)
- **Purpose:** Per-client daily usage (message counts, escalations, AI calls) for billing insight.
- **Auth:** `X-Internal-API-Key` + owner-only.
- **Response:** list of `{ shop_id, metric, count }` for that client/day (from `usage_daily`).

## Celery task interfaces (sketch)

| Task | Input | Stage |
|------|-------|-------|
| `process_whatsapp_message` | `shop_id`, `phone`, `body`, `media`, `message_sid` | 3 |
| `send_telegram_alert` | `telegram_id`, `text` | 2 |
| `health_check` (beat, 60s) | — | 10 |
| `generate_order_excel` | `shop_id`, `filter`, `detailed?` | 9 |

## LLM provider config interface (ADR-004)

The `llm/llm_client.py` exposes one async chat-with-tools entrypoint. Provider selected by env:

| Env | Testing (ADR-004 rev.2) | Production |
|-----|------------------------|------------|
| `AI_PROVIDER` | `moonshot` | `openai` |
| `AI_BASE_URL` | `https://api.moonshot.ai/v1` (global; `.cn` 401s) | `https://api.openai.com/v1` |
| `AI_MODEL` | `kimi-k2.6` (`kimi-k2.7-code*` = code-only, don't use) | `gpt-4o` |
| `AI_API_KEY` | Moonshot key | OpenAI key |
| `AI_TEMPERATURE` | **`1.0` — `kimi-k2.*` 400s on anything else** | `0.2` |

Function/tool definitions (`search_products`, etc.) are provider-agnostic JSON schemas (OpenAI tool-calling format, compatible with Moonshot).

## Testing topology (ADR-005)

Telegram-first (ADR-002); WhatsApp mocked. **Bots cannot message bots**, so all chatters are user accounts scripted via Telethon.

### Bots (tokens) — 3
| Bot | Role | Resolves shop via |
|-----|------|-------------------|
| `staff` | owner + shopkeepers | `telegram_id → shop_id` (shopkeepers); owner = `OWNER_TELEGRAM_ID` |
| `shop1` | customer-facing, shop 1 | bot identity (mirrors Twilio `To`) |
| `shop2` | customer-facing, shop 2 | bot identity |

### User accounts — 2
| Account | Plays | Notes |
|---------|-------|-------|
| A | Owner + Customer(shop1) + Customer(shop2) | different bots, no conflict |
| B | Shopkeeper(shop1) | used to prove cross-shop denial |

Customer identity in tests = `telegram_id` (prod = phone number); both flow into the same `customer_identity` field, so Stage 13 only swaps the resolver.

## Versioning

- Policy: none yet (internal system). If a public API emerges, adopt URL-prefix versioning (`/v1/`).
