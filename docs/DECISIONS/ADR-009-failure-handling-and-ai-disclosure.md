# ADR-009 — Failure handling & AI disclosure: the customer never meets the machine

- **Status:** Accepted
- **Date:** 2026-07-08
- **Deciders:** owner
- **Stage when decided:** 5 (implemented in `ai/`; the notification transport lands in Stage 6)

## Context

The Stage 4 failure path replied to the customer with:

> "Sorry, I'm having trouble right now. Please try again in a moment."

Three things are wrong with that sentence. It **announces that a machine is answering** — no shop
assistant talks like that. It **asks the customer to retry into a system that is already broken**.
And it leaves the shop silently losing conversations while nobody who could fix it is told.

The owner's directive: *"do not say customer like this that he is using AI. If anything happen send
human, do not tell both, just inform to owner about problem and what action system takes."*

Three audiences, three different truths:

| Audience | What they learn |
|----------|-----------------|
| **Customer** | "Let me connect you with our specialist." Nothing else, ever. |
| **Shopkeeper** | A customer needs them. No technical detail. |
| **Owner** | Exactly what broke, and exactly what the system did about it. |

## Decision

**1. There is no error message to the customer.** `FALLBACK_REPLY` is deleted, not reworded. Every
path where the AI cannot answer — a deliberate `escalate_to_human`, an LLM outage, an empty model
response — emits the *identical* `ESCALATION_REPLY`. A customer cannot distinguish a crash from a
routine handoff, because the bytes are the same.

SPEC §11 says "retry once, then fallback message". Satisfied: **the fallback is a human, not an apology.**

**2. One exit.** `ai/orchestrator._handoff_to_human()` is the only place a non-answer is produced.
Both the escalation path and every failure path route through it — one guard, all callers. A future
failure mode cannot invent its own customer-facing wording.

**3. The owner alone is paged, and only for real problems.** `_handoff_to_human(problem=...)`
triggers `_alert_owner()` with the exception and the action taken. A refund request is business as
usual and pages nobody; an LLM outage pages the owner. Tested both ways.

**4. Alerting must never cost the customer their reply.** `_alert_owner` runs *inside* an `except`
block. Once it sends real Telegram (Stage 6) it can fail on its own — so it is wrapped: if the alert
throws, it is logged and the customer still gets their handoff. Pinned by
`test_a_failing_owner_alert_still_leaves_the_customer_answered`.

**5. AI disclosure — do not volunteer, do not lie.** The system prompt forbids mentioning AI, bots,
automation, models, systems, databases, searching, tools, or errors. The assistant speaks as the shop.

But if a customer **sincerely asks** whether they are talking to a human or a machine, the model must
**neither deny nor confirm** — it calls `escalate_to_human` and a person takes over. Verified live:
*"are you a real person or a bot?"* → handoff.

## Rationale

Not volunteering that a shop uses automation is ordinary commercial practice. **Actively denying it
when asked is deception**, and it is regulated: California's B.O.T. Act (BPC §17940 et seq.) requires
disclosure when a bot incentivises a sale, and EU AI Act Art. 50 requires that people be informed they
are interacting with an AI system unless it is obvious. Instructing the model to lie would expose the
owner to that risk and would be the kind of instruction that quietly rots a product.

Handing the question to a human resolves it cleanly: **no disclosure text we authored, and no lie.**
The customer who cares enough to ask gets a person, which is what they were asking for anyway.

## Consequences

- Positive: no customer-facing string reveals the machine. The failure UX is indistinguishable from
  the success-adjacent UX (a handoff), which is also simply better product design.
- Positive: the owner learns about outages from the alert, not from a drop in sales.
- Negative: a customer in a genuine outage is promised a specialist. **Stage 6 must actually deliver
  them one** — until the shopkeeper notification is wired, that promise is only logged
  (`ponytail:` markers at `orchestrator.py:50` and `:67`). This is the single most important
  follow-through in Stage 6.
- Negative: "cannot distinguish a crash from an escalation" is true for the shopkeeper too. If
  outage volume ever matters to them, the shopkeeper notice would need a (still non-technical) hint.
- Open: **Q-016** — whether the shop should proactively disclose automation at conversation start
  (jurisdiction-dependent; the owner sells into UAE, but the code is generic).

## Related

- SPEC §3 (escalation, "Let me connect you with our specialist"), §11 (retry once then fallback).
- ADR-008 (tool surface; `escalate_to_human`). Stage 6 (escalations) wires the notifications.
- `src/app/llm/prompts.py`, `src/app/ai/orchestrator.py`, `tests/ai/test_orchestrator.py`.
