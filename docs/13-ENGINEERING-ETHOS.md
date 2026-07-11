# 13 — Engineering Ethos

> Mandatory engineering rules for this project, distilled from three skill sets.
> Every LLM working here follows these. Source skills: `caveman`, `karpathy-guidelines`, `ponytail` (+ `ponytail-review`, `ponytail-audit`, `ponytail-debt`).

## A. Communication — Caveman (prose only)

- Responses are **terse**: drop filler, hedging, pleasantries, decorative tables/emoji. Fragments OK. Short synonyms.
- **Code, commits, PRs, error strings, API names: write normal.** Caveman governs prose, not code.
- Auto-clarity: drop caveman for **security warnings, irreversible-action confirmations, multi-step sequences where order could misread, or when compression creates ambiguity.** Resume after.
- Preserve the user's language; compress style, not language.

## B. Coding — Karpathy guidelines

1. **Think before coding.** State assumptions explicitly. If multiple interpretations exist, present them — don't pick silently. If unclear, stop and ask.
2. **Simplicity first.** Minimum code that solves the problem. No speculative features, no abstractions for single-use code, no unrequested flexibility, no error handling for impossible scenarios. If 200 lines could be 50, rewrite.
3. **Surgical changes.** Touch only what you must. Don't "improve" adjacent code. Match existing style. Remove only the orphans YOUR change created; mention pre-existing dead code, don't delete it. Every changed line traces to the request.
4. **Goal-driven.** Transform tasks into verifiable goals: "fix bug" → "write a test that reproduces it, then make it pass." Loop until verified. State a brief plan with per-step verify checks for multi-step work.

## C. Coding — Ponytail (lazy senior dev)

The ladder — stop at the first rung that holds (run it *after* understanding the problem, not instead):
1. Does this need to exist at all? (YAGNI) Speculative = skip, say so in one line.
2. Already in this codebase? Reuse it. Look before you write.
3. Stdlib does it? Use it.
4. Native platform feature covers it? Use it (DB constraint > app code, `<input type=date>` > picker lib).
5. Already-installed dependency solves it? Use it. Never add a new dep for what a few lines can do.
6. Can it be one line? One line.
7. Only then: the minimum code that works.

Rules:
- No unrequested abstractions: no interface with one implementation, no factory for one product, no config for a value that never changes.
- Deletion over addition. Boring over clever.
- Fewest files possible. Shortest working diff wins — but only once you understand the problem.
- **Bug fix = root cause.** Grep every caller of the function you're about to touch. Fix once where all callers route through.
- Mark deliberate simplifications with a `ponytail:` comment naming **ceiling + upgrade path**: `# ponytail: global lock, per-account locks if throughput matters`.
- Non-trivial logic (branch/loop/parser/money/security) leaves **ONE runnable check** behind — smallest `assert`-self-check or one `test_*.py`. Trivial one-liners need no test.
- Never simplify away: input validation at trust boundaries, error handling that prevents data loss, security, accessibility, anything explicitly requested.

## D. Review & debt (ponytail-review / -audit / -debt)

- **Per stage (before handoff):** run a `ponytail-review` pass on the stage's diff — one line per over-engineering finding (`delete:` / `stdlib:` / `native:` / `yagni:` / `shrink:`). The diff's best outcome is getting shorter. `net: -<N> lines possible.`
- **Periodically / Stage 12:** run `ponytail-audit` repo-wide for over-engineering.
- **Debt ledger:** every `ponytail:` comment is harvested into `PONYTAIL-DEBT.md` so deferrals are tracked, not forgotten. Any `ponytail:` comment with no upgrade trigger is tagged `no-trigger` (rot risk). Update the ledger at stage end.

## E. Scope boundaries

- ponytail-review/audit scope = **over-engineering & complexity only.** Correctness bugs, security holes, performance → normal review pass. Never flag a single smoke test / `assert` self-check for deletion.
- A single runnable check is the ponytail minimum, not bloat.

## F. Activate / deactivate

- Active by default for every LLM on this project (it's in the docs, not a session flag).
- Human says "stop caveman" / "stop ponytail" / "normal mode" → revert that skill for the session.
