# Module: ai

## Responsibility
AI orchestration: anti-hallucination system prompt, execute `search_products` tool calls against `products/`, relevance×boost scoring, promotion logic, out-of-domain → escalation. Tested against Moonshot (ADR-004).

## Boundaries
- **Owns:** the conversation loop with the LLM, tool-call execution, response composition.
- **Exposes:** `answer_customer(shop, identity, message, redis, media_sink=None) -> reply_text`. `media_sink` (optional list) receives any product photos/video the model chose to show; the return stays a plain string.
- **Does NOT touch:** LLM HTTP client (in `llm/`); product DB queries (in `products/`).

## Key files
| Path | Role | Stage |
|------|------|-------|
| `orchestrator.py` | chat loop + tool execution + escalation short-circuit | 4 ✅ |

**Three planned files were deliberately not written** (ponytail-review, Stage 4):
- `spec_search.py` → duplicate of `products/search.py`; `products/` owns product queries (see Boundaries).
- `promotion.py` → SPEC §5 promotion is *system-prompt text* (`llm/prompts.py`), not code. Ranking already happened in `products/search.py`.
- `escalation_detect.py` → the model calls the `escalate_to_human` tool; no keyword classifier needed (ADR-008).

## Status
🟢 Stage 4 ✅ — `answer_customer` live. **Bounded tool loop (≤3 rounds)** so the model can search → `show_product_media` → answer. Five tools: `search_products`, `escalate_to_human` (short-circuits — no search, no further rounds, returns SPEC §3's specialist line), `place_order`, `request_price`, `show_product_media` (fills `media_sink`; the channel adapter sends the photos). Prompt is **consultative** — qualify first, show ≤3 matches, recommend when the customer is unsure (fixed in the live walkthrough; the old prompt dumped the whole catalogue).

**Security invariant (ADR-008):** `_serialize()` never emits `boost_level` — what isn't sent can't leak. `tags` ARE sent (the model needs them to phrase "clearance deal"); the prompt forbids echoing them verbatim. Money crosses as a string, never float.

**Truth invariant (ADR-008 rev. 2, Q-015):** grounding the model in a tool makes it exactly as truthful as the tool. Anti-hallucination prompting constrains the model's *memory*, not the tool's *completeness*. A default `search_products` call returns a boost-ranked, truncated slice — so **superlatives cannot be answered from it**. The prompt forbids "cheapest"/"most expensive"/"the only"/"nothing under X" without a matching `sort` search; `_run_tool` validates the `sort` enum and falls back to `relevance` (models improvise enum values).

**Failure invariant (ADR-009):** the customer never learns a system exists. `_handoff_to_human()` is the **single exit** for "the AI is not answering this" — deliberate escalation, LLM outage, and empty model response all return the identical `ESCALATION_REPLY`. There is no error message and no `FALLBACK_REPLY`. Only `_alert_owner()` carries the technical truth (problem + action taken), and only for real failures — a refund request pages nobody. The owner alert is wrapped in `try/except`: it runs inside an `except` block, and a failing alert must never cost the customer their reply.

**Multi-turn since Stage 6.** `_replay()` loads the Redis session (`escalations.context`) on every message, so the AI remembers the conversation — including the turns a shopkeeper handled while the AI was frozen. A shopkeeper's turn replays as an *assistant* turn: the AI resumes a conversation a human was holding, without being told a human held it. An unreadable session degrades to a single-turn answer, never to a handoff.

**The ADR-009 debt is paid (Stage 6).** `_handoff_to_human()` now really calls `escalations.escalate()` — a `pending_escalations` row, the AI frozen for that customer, the shop's staff notified — and `alert_owner()` really pages the owner over Telegram. Both are wrapped: they do network I/O on the reply path, and neither may cost the customer their reply.

Verified live on `moonshotai/kimi-k2`: product question → `search_products`; refund request → `escalate_to_human`. Re-run that check when switching provider (Stage 12 → GPT-4o).

Spec ref: §3, §4, §5. ADR-004, ADR-008.
