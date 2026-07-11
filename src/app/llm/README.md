# Module: llm

## Responsibility
Provider-agnostic LLM access (ADR-004). One async `LLMClient` over the OpenAI-compatible API; provider selected by env (Moonshot for testing, OpenAI GPT-4o for prod). Tool/function definitions and system prompts live here.

## Boundaries
- **Owns:** the LLM HTTP client + tool schemas + prompts.
- **Exposes:** `get_llm_client()`, `LLMClient.chat(messages, tools)`, function definitions.
- **Does NOT touch:** product DB queries (those live in `products/`); the `llm` module only *declares* the `search_products` tool, the `ai/` module *executes* it.

## Key files
| Path | Role | Stage |
|------|------|-------|
| `llm_client.py` | provider-agnostic client: `chat()` + tool-calling + retry-once | 4 ✅ |
| `functions.py` | `search_products` + `escalate_to_human` tool schemas | 4 ✅ |
| `prompts.py` | anti-hallucination + superlative + promotion + no-AI-disclosure prompt, `ESCALATION_REPLY` | 4 ✅ |

## Status
🟢 Stage 4 ✅ — `chat()` implemented via `AsyncOpenAI` pointed at `settings.ai_base_url`. **Transport retry-once lives here** (SPEC §11); the user-facing fallback message is composed by `ai/orchestrator.py`. Unparsable tool arguments degrade to `{}` rather than crash.

## Notes
- **There is no `FALLBACK_REPLY`** (ADR-009). It said *"Sorry, I'm having trouble right now"* — which announced that a machine was answering and told the customer to retry into a broken system. Deleted, not reworded. Every non-answer emits `ESCALATION_REPLY`, so a crash and a deliberate handoff are byte-identical to the customer. `tests/ai/test_orchestrator.py::test_no_fallback_reply_constant_survives` keeps it deleted.
- The prompt forbids mentioning AI/bots/automation/systems/errors. If a customer sincerely asks whether they're talking to a machine, the model calls `escalate_to_human` — **never denies, never confirms, never lies** (ADR-009; see Q-016 for proactive disclosure).
- Function-calling parity **verified live** on official Moonshot `kimi-k2.6` (ADR-004 rev.2). Re-verify when flipping to GPT-4o (Stage 12). `kimi-k2.*` requires `AI_TEMPERATURE=1.0`.
- Never expose `boost_level`/`tags` to customers (SPEC §5). Enforced structurally, not by prompt: `ai/orchestrator._serialize()` omits `boost_level` from the tool result entirely (ADR-008). Prompts only cover the softer "don't echo tags verbatim" rule.
- Retry lives in `chat()`, not in callers — one guard where all call sites route through.
