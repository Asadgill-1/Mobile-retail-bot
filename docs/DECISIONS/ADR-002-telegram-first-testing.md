# ADR-002 — Telegram-first testing; WhatsApp at pre-deploy

- **Status:** Accepted
- **Date:** 2026-07-07
- **Deciders:** owner
- **Stage when decided:** 0

## Context

The system has two messaging channels: WhatsApp (Twilio, customer-facing) and Telegram (owner + shopkeepers). WhatsApp requires provisioning one Twilio number per shop (30 numbers) plus Twilio/WhatsApp approval — slow and costly. The owner stated: "on testing phase we use telegram on all things then when deploy the put whatsapp."

## Options considered

### Option A — Telegram-first; WhatsApp mocked until pre-deploy
- Pros: start testing immediately with one Telegram bot; no Twilio provisioning cost during dev; the 9-step pipeline can be exercised end-to-end through Telegram.
- Cons: WhatsApp-specific paths (Twilio signature, `To`→shop lookup, form-encoded webhooks, MessageSid dedup) are not validated until late.

### Option B — Both channels from the start
- Pros: WhatsApp paths tested continuously.
- Cons: requires Twilio numbers early; cost; approval delay blocks dev.

### Option C — WhatsApp-only via Twilio sandbox
- Pros: tests the real customer path.
- Cons: sandbox limits; still needs Twilio; ignores owner/shopkeeper Telegram flows.

## Decision

Adopt **Option A** — Telegram-first during testing. WhatsApp is mocked/simulated until Stage 13 (pre-deploy), where the real Twilio path is activated.

## Rationale

Owner directive; unblocks development without Twilio provisioning. Telegram exercises command surface (owner/shopkeeper) and, with a test customer bot, can drive the same message pipeline. The WhatsApp path is built (Stage 3) but its external integration is activated only at Stage 13.

## Consequences

- Positive: no Twilio spend during testing; faster iteration.
- Negative: WhatsApp/Twilio-specific bugs surface only at Stage 13 → mitigate by building the WhatsApp module with signature verification unit tests + a mock Twilio client early.
- Follow-ups: Stage 13 cutover checklist; ensure `whatsapp/` module is mockable behind an interface.

## Related

- ADR-001 (stack), Q-004 (Twilio provisioning).
- SPEC §1, §9, final note.
