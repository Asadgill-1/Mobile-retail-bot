# ADR-008 — AI tool surface: two tools, escalation by tool-call, boost hidden from the model

- **Status:** Accepted (revised 2026-07-08 — see "Revision 2: price ordering")
- **Date:** 2026-07-07
- **Deciders:** Stage 4 (implementation), pending owner confirmation on Q-012/Q-013
- **Stage when decided:** 4

## Context

SPEC §3 requires two AI behaviours that must never fail:

1. **Anti-hallucination** — the model may only speak about products returned by `search_products`.
2. **Out-of-domain refusal** — refunds, complaints, repairs, negotiations, legal questions and
   "talk to a human" must be handed to a person, never answered by the AI.

SPEC §5 adds a third: the model should mention a product's tags *naturally* ("clearance" →
"special clearance deal") while **never exposing `boost_level` or raw tags** to the customer.

These three pull against each other: the model needs tag information to promote well, but the
same information must not leak, and it must know when to stop answering entirely.

## Options considered

### Option A — Keyword classifier for out-of-domain, single `search_products` tool
- Pros: deterministic, testable without an LLM; no reliance on model judgement.
- Cons: a keyword list never covers phrasing variety ("this thing is broken, sort it out");
  a second code path to maintain; the model still answers whatever the classifier misses.

### Option B — Two tools; the model calls `escalate_to_human` itself
- Pros: no classifier to write or maintain; the model already reads intent; escalation reason
  comes free as a tool argument; verified live against Moonshot.
- Cons: depends on the model honouring the system prompt; a bad model could answer anyway.

### Option C — Pass `boost_level` to the model and instruct it not to reveal it
- Pros: model could reason about promotion strength.
- Cons: the only thing preventing a leak is a prompt instruction. Ranking already applied boost —
  the model gains nothing and can only leak.

## Decision

**Option B, plus withhold `boost_level` entirely (reject Option C).**

- Tools: `search_products(requirements)` and `escalate_to_human(reason)` — `llm/functions.py`.
- Escalation short-circuits: if the model calls `escalate_to_human`, the orchestrator fires the
  escalation hook and returns SPEC §3's "Let me connect you with our specialist." No second LLM
  round, no product search.
- Tool results serialize **without `boost_level`** and **with `tags`** (`ai/orchestrator._serialize`).
  The model needs tags for SPEC §5 phrasing; it never needs the boost number, because ranking in
  `products/search.py` already consumed it. What is not sent cannot leak.
- Money crosses the tool boundary as a string (`price_aed: "1500.00"`), never a float (CONVENTIONS).

## Rationale

Prompt instructions are a request; omitting the field is a guarantee. Boost is a ranking input, not
a presentation input — once `rank()` has ordered the list, the number has done its whole job. Tags
are different: they change the *wording* the model should choose, so they must cross.

Escalation-by-tool-call was verified live on `moonshotai/kimi-k2`: a product question produced
`search_products{requirements: "Samsung phone with a good camera"}` and "I want a refund" produced
`escalate_to_human{reason: "refund request"}`. The classifier in Option A would have been more code
for strictly worse coverage.

## Consequences

- Positive: `boost_level` leakage is structurally impossible, not prompt-dependent. No classifier module
  (`ai/escalation_detect.py` and `ai/promotion.py` were dropped from the planned file list — promotion is
  prompt text, detection is a tool call).
- Negative: out-of-domain refusal now depends on model quality. **Mitigation:** the live check in the
  Stage 4 verification asserts both behaviours; re-run it when switching provider (Stage 12 → GPT-4o).
- Negative: a model that ignores the prompt could still paraphrase a tag verbatim. Accepted — tags are
  not secrets, boost levels are.
- Follow-up: Stage 6 replaces the `_notify_escalation` logging stub with `pending_escalations` +
  shopkeeper Telegram alert + AI freeze (`ponytail:` marker in `ai/orchestrator.py`).
- Follow-up: single-turn today. Conversation history (Redis session) arrives with Stage 6 handover /
  Stage 7 last-25-messages capture.

---

## Revision 2 (2026-07-08) — price ordering, and why grounding wasn't enough

### What happened

Running the real model against a realistic seeded catalogue (`scripts/seed_test_catalog.py`), a
customer asked *"what's your cheapest phone?"* and the AI answered *"the Refurbished S23 Ultra at
2,899 AED"*. A **Galaxy S23 at 2,499 AED** was in stock and cheaper.

**The model hallucinated nothing.** It obeyed the anti-hallucination rule, called `search_products`,
received five products, and truthfully reported the cheapest *of those five*. The falsehood was
manufactured underneath it: `search_products` returned a **boost-ranked, truncated** slice and
described itself as nothing in particular. The 2,499 AED phone was never in the payload — it was
unboosted, and "cheapest"/"phone" matched no spec or tag, so every row scored 0 relevance and the
list collapsed to pure boost order. Boost, whose entire job is to promote, had hidden a cheaper product.

Two lessons, both now encoded:

1. **Grounding a model in a tool makes it exactly as truthful as the tool.** Anti-hallucination
   prompting constrains the model's *memory*; it says nothing about the tool's *completeness*.
2. **Superlatives require a complete or correctly-ordered view.** "Cheapest", "most expensive",
   "the only one", "anything under X" can never be answered from a top-N relevance slice.

### Decision

- `search_products` gains `sort: "relevance" | "price_asc" | "price_desc"` and `max_price_aed: number`.
- **Price ordering ignores `boost_level` entirely.** A promoted product must not be able to hide a
  cheaper one. Boost promotes; it does not lie. (Relevance ordering is unchanged — boost still
  applies there, per SPEC §4/§5.)
- A price sort still respects what the customer described: rows matching the `requirements` are
  preferred, and only if nothing matches does it order the whole in-stock catalogue. So
  *"cheapest Samsung"* never returns a cheaper Apple.
- The system prompt now carries a **superlative rule**: the model may not say cheapest / most
  expensive / lowest / "the only" / "nothing under X", or compare across the range, unless it ran the
  matching sorted search. It is told explicitly that a default search shows a slice, not the catalogue.
- Customer vocabulary is mapped onto the schema in `products/search.py::_SYNONYMS`
  (`phone→mobile`, `cheap/cheapest→budget`, `deal→clearance`, `notebook→laptop`, …). Without this,
  the words customers actually use score 0 relevance against every row.
- The orchestrator validates `sort` against the enum and falls back to `relevance`: models improvise
  enum values, and junk must never reach the query layer.

### Consequences

- Positive: the defect class is closed at the tool boundary, not patched with prompt text alone.
  Regression tests pin it (`test_cheapest_ignores_boost_the_q015_regression`,
  `test_price_sort_still_respects_what_the_customer_asked_for`).
- Positive: `max_price_aed` makes "under 2600" a filter rather than a hope.
- Negative: a shopkeeper's boost no longer influences price-sorted answers. That is the point, and
  it is a deliberate trade of promotion against honesty.
- Negative: superlative correctness still depends on the model choosing `sort`. Mitigated by the tool
  *description* (not just the system prompt) stating that a default search cannot answer superlatives,
  and verified live on `moonshotai/kimi-k2`. Re-verify when switching provider (Stage 12 → GPT-4o).
- Open: `_SYNONYMS` is a hand-written list. It will need extending as real customer language arrives.

## Related

- SPEC §3 (anti-hallucination, escalation), §4 (search), §5 (promotion, never expose boost/tags).
- ADR-004 (LLM provider abstraction). Q-012, Q-013 (ranking + searchable-field ambiguities).
- **Q-015** (this revision — price blindness and category synonyms).
- `05-CONVENTIONS.md` anti-patterns: "Exposing `boost_level` / `tags` to customers".
