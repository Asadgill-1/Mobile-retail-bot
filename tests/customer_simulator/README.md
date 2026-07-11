# tests/customer_simulator

> Test harness that drives the system as real Telegram *users* (not bots), per ADR-005.
> Built in Stage 2/3. Referenced here so the next LLM knows where customer-side test traffic comes from.

## Why this exists

Telegram **bots cannot message other bots**. Customers and shopkeepers must be real user accounts. This harness uses **Telethon** user-sessions (real phone-number logins, stored as `.session` files) to script messages from the two test accounts to the three test bots.

## Topology (ADR-005)

- **Account A** → Owner (staff bot) + Customer of shop 1 (shop1 bot) + Customer of shop 2 (shop2 bot)
- **Account B** → Shopkeeper of shop 1 (staff bot)

## Planned files (Stage 2/3)

| Path | Role |
|------|------|
| `sessions/` | Telethon `.session` files (gitignored) |
| `userbot.py` | Telethon client factory per account |
| `senders.py` | helpers: `send_as_customer(shop, text)`, `send_as_owner(cmd)`, `send_as_shopkeeper(cmd)` |
| `scenarios/` | scripted end-to-end flows (browse, escalate, attack, suspend) |

## Setup (one-time per account)

```bash
pip install -r requirements-dev.txt
# Log in each account once to create .session files:
python -m tests.customer_simulator.login  # prompts for phone + code per account
```

## Notes

- `.session` files are secrets — already in `.gitignore`.
- Customer identity in tests = `telegram_id`; in prod = phone number. The harness sets the customer-identity field so Stage 13 only swaps the resolver.
