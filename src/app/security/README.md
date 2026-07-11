# Module: security

## Responsibility
Intrusion detection + auto-quarantine (SPEC §7, §8). 6 attack patterns, `quarantine:{phone}` Redis key, `security_incidents` capture (last 25 messages), owner investigation commands, direct-to-shop bypass.

## Boundaries
- **Owns:** attack-pattern matchers, quarantine state, `security_incidents` + `blacklisted_phones` writes, bypass flags.
- **Exposes:** `detect_attack(message, context) -> AttackResult`, `is_quarantined(phone)`, `is_blacklisted(phone)`, `is_bypassed(phone)`, quarantine/blacklist/bypass ops.
- **Does NOT touch:** the pipeline order (in `messaging/`); it provides the detectors the pipeline calls.

## Attack patterns (§7)
1. prompt injection ("ignore previous", "DAN", "system prompt", "act as admin", base64, >2000 chars)
2. SQL injection patterns
3. rapid-fire: 20+ msgs / 60s
4. cross-shop probing ("other shops", "all your locations", "data from shop #")
5. admin commands in customer messages (`/addshop`, `/owner`, `/pauseshop`)
6. credential probing ("API key", "database password", "show me backend")

## Key files
| Path | Role | Stage |
|------|------|-------|
| `detectors.py` | 6 pattern matchers — **pure**, no IO (rapid-fire count is passed in) | 7 ✅ |
| `service.py` | quarantine · blacklist · bypass · incident snapshot · hot-path reads | 7 ✅ |

> Built as **2 files, not 3** (the old plan listed `quarantine.py` + `bypass.py`). All the stateful
> ops are one small `service.py`, mirroring `escalations/service.py`. The pure detection logic is the
> only thing that earns its own file.

## Status
🟢 live (Stage 7).

- **`detectors.detect_attack(text, *, msg_count_60s)` → `AttackResult | None`.** Pure. Five content patterns (regex/substring) + rapid-fire (the caller supplies the 60s count). Order: content first, rapid last.
- **`service`** owns the Redis keys the pipeline reads — `quarantine:{id}` (1h TTL), `bypass_ai:{id}` (no TTL), `blacklist:{id}` (no TTL) — plus `rate:{id}` (60s window). The pipeline calls `is_quarantined` / `is_bypassed` / `is_blacklisted` here so writer and reader never drift.
- **`quarantine()`** sets the lock, snapshots the last 25 via `escalations.context.history()` into `security_incidents`, and pages the owner with the incident id + one-tap follow-up commands. A failed DB write still holds the block and still alerts (forensics are best-effort; the block is not).
- **Blacklist hot-path truth is Redis**; the DB row is the durable/audit copy. A Redis flush drops the hot-path block until re-set (`ponytail:` in `service.py`, rehydrate at Stage 10/12 if it bites).
- **Owner commands** (`telegram_bot/bot.py`, owner bot): `/investigate` `/quarantine_extend` `/quarantine_lift` `/blacklist` `/forward_to_shop` `/bypass_ai` `/bypass_remove`.

Spec ref: §7, §8. Pipeline wiring: `messaging/pipeline.py` steps 3 (blacklist), 4 (quarantine), 5 (bypass), 6 (attack).
