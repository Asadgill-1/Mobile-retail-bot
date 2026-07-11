# ADR-004 — LLM provider abstraction (Moonshot direct for test / OpenAI GPT-4o prod)

- **Status:** Accepted (revised 2026-07-08 — testing provider is the **official Moonshot API, direct**; see Revision 2)
- **Date:** 2026-07-07
- **Deciders:** owner
- **Stage when decided:** 0 (rev.1 at Stage 2→3: OpenRouter key; rev.2 at Stage 5: official Moonshot key)

## Context

SPEC §3, §4 mandate GPT-4o with function calling as the only source of product data (anti-hallucination). The owner stated: "in place of openAI use moonshot" during the testing phase. The owner later supplied an **OpenRouter** API key (OpenAI-compatible aggregator) and confirmed it routes to Moonshot/Kimi models — so testing uses OpenRouter → `moonshotai/kimi-k2`, honoring the "use Moonshot for testing" directive via a single OpenAI-compatible client. Hardcoding any one vendor blocks cheap/fast testing and couples the codebase to it.

## Options considered

### Option A — Hardcode OpenAI; swap later
- Pros: simplest now.
- Cons: testing costs; vendor lock-in; risky late swap.

### Option B — Provider-agnostic client behind one interface; config selects provider
- Pros: switch by env; test on Moonshot, prod on GPT-4o; isolated `llm/` module.
- Cons: must verify function-calling parity across providers; slight abstraction overhead.

### Option C — Multiple provider clients with branching everywhere
- Pros: explicit.
- Cons: branching pollutes call sites; high maintenance.

## Decision

Adopt **Option B** — a single provider-agnostic LLM client (`src/app/llm/llm_client.py`) using the **OpenAI-compatible API** surface. OpenRouter, Moonshot, and OpenAI all expose the OpenAI-compatible Chat Completions + tool-calling API, so one client with configurable `AI_PROVIDER`, `AI_BASE_URL`, `AI_API_KEY`, `AI_MODEL` works for all. Tool/function definitions are written once in OpenAI tool-calling JSON-schema format.

- **Testing (now wired, Q-005 resolved):** `AI_PROVIDER=openrouter`, `AI_BASE_URL=https://openrouter.ai/api/v1`, `AI_MODEL=moonshotai/kimi-k2`. Verified: a real chat call returns 200 + a completion.
- **Production:** `AI_PROVIDER=openai`, `AI_BASE_URL=https://api.openai.com/v1`, `AI_MODEL=gpt-4o` (Stage 12).
- **Alt:** `AI_PROVIDER=moonshot`, `AI_BASE_URL=https://api.moonshot.ai/v1` for direct Moonshot access (no OpenRouter middleman) if desired.

## Rationale

Owner directive (Moonshot in testing), delivered via OpenRouter (one OpenAI-compatible key that routes to `moonshotai/kimi-k2`). Avoids vendor lock-in and late risky swaps. OpenAI-compatible API means near-zero abstraction cost. Function-calling is the anti-hallucination core (§3), so we verify parity explicitly in Stage 4 tests.

## Consequences

- Positive: cheap testing; clean `llm/` boundary; future providers (Anthropic, local) easy to add.
- Negative: must verify Moonshot tool-calling (via OpenRouter) behaves like OpenAI (Stage 4); per-provider quirks may need small adapters; OpenRouter adds a hop (latency + a third party).
- Follow-ups: Stage 4 implements `LLMClient.chat()` (real function-calling + anti-hallucination + `search_products` tool) and verifies tool-calling on `moonshotai/kimi-k2` via OpenRouter; Stage 12 flips `AI_PROVIDER` to openai. Q-005 resolved (OpenRouter key supplied).

---

## Revision 2 (2026-07-08) — OpenRouter dropped; official Moonshot API, direct

### Context

The owner reported problems with OpenRouter and supplied an **official Moonshot API key**
("use that as model because open router have problem"). The provider abstraction (Option B)
made this a config change: no call site branches on provider, so only `.env` + defaults moved.

### Decision

- **Testing:** `AI_PROVIDER=moonshot`, `AI_BASE_URL=https://api.moonshot.ai/v1`, `AI_MODEL=kimi-k2.6`.
- OpenRouter remains supported as an aggregator fallback; nothing in the code was removed.
- Code defaults in `config/settings.py` were moved to match, so a deployment without a full
  `.env` is self-consistent rather than silently broken.

### What the API actually exposes (probed, not assumed)

- `api.moonshot.ai` is the **global** endpoint. `api.moonshot.cn` returns **401** for a global key.
- Models: `kimi-k2.5`, `kimi-k2.6`, `kimi-k2.7-code`, `kimi-k2.7-code-highspeed`,
  `moonshot-v1-{8k,32k,128k}` (+ vision-preview variants), `moonshot-v1-auto`.
- **`kimi-k2.7-code*` are code-specialised — not for customer chat.** Chose `kimi-k2.6`
  (newest general model, closest successor to the `moonshotai/kimi-k2` used via OpenRouter).

### The footgun

**`kimi-k2.*` rejects any `temperature` other than `1`** — HTTP 400 `"invalid temperature: only 1 is
allowed"`. The previous config used `0.2`. Left unchanged, *every* customer message would 400,
retry once (SPEC §11), raise, and be swallowed by `answer_customer` into `FALLBACK_REPLY`: loud in
the logs, invisible to the customer, and the bot would look merely "flaky".

`AI_TEMPERATURE=1.0` is now set in `.env`, documented in `.env.example`, and is the code default.
`moonshot-v1-*` accepts `0.2` if a lower temperature is ever needed.

### Verification (live, not assumed)

Re-ran the Stage 4 acceptance and the Q-015 regression against `kimi-k2.6`:
- product question → `search_products{requirements: "Samsung phone with a good camera"}` (no hallucination)
- `"I want my money back"` → `escalate_to_human{reason: "refund request"}` (out-of-domain refused)
- `"cheapest phone?"` → **2,499 AED Galaxy S23** (Q-015 holds: model emitted `sort: price_asc`)
- `"anything under 2600?"` → offers the 2,499 and nothing above (`max_price_aed` honoured)
- `"most expensive?"` → **4,699 AED S23 Ultra 512GB**
- no `boost_level` / raw tag / internal-field leakage

### Consequences

- Positive: one less hop and one less third party on the customer path (latency, reliability, billing).
- Positive: the abstraction paid for itself — a provider swap touched `.env`, `settings.py` defaults,
  and docs. **Zero changes to `llm_client.py`, `ai/`, or any call site.**
- Negative: Moonshot per-model quirks are now ours to track (the `temperature` constraint is the
  first; there will be others). Vision variants exist but are unused.
- The owner's `key.txt` was consumed into the gitignored `.env` and **deleted**, per the Stage 2
  precedent for delivered credentials.

## Related

- ADR-001 (stack), `11-API-CONTRACTS.md` (LLM config table), `src/app/llm/llm_client.py`.
- ADR-008 rev.2 / Q-015 (superlative + price ordering) — re-verified on this provider.
- SPEC §3, §4.
