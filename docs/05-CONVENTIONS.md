# 05 — Conventions

> Rules any LLM/human MUST follow when writing code here. Match existing code first; this fills gaps.
> **Engineering ethos:** see `docs/13-ENGINEERING-ETHOS.md` (caveman prose, karpathy surgical/simplicity, ponytail lazy ladder). The coding rules below overlap with it; both apply.

## Language & style

- Primary language: **Python 3.11+** (ADR-001).
- Linter/formatter: **ruff** + **black** — config in `pyproject.toml` (Stage 12 finalize).
- Style summary:
  - Indentation: **4 spaces**
  - Naming: `snake_case` for funcs/vars/modules; `PascalCase` for classes; `UPPER_SNAKE` for constants.
  - Max line length: **100**
  - Type hints: **required** on all public functions (async signatures included).
  - Async-first: I/O-bound paths are `async def`; CPU-bound/long work goes to Celery.

## File & folder naming

- Python modules: `snake_case.py`.
- One module = one folder under `src/app/<module>/`, with a `README.md` (from `docs/TEMPLATES/MODULE-DOC.md`).
- Tests: `tests/<module>/test_<thing>.py`, mirroring `src/app/`.
- Config/env: `config/`; migrations: `migrations/`; scripts: `scripts/`. **No loose root files** except `README.md`, `AGENTS.md`, and standard manifests (`.env.example`, `Dockerfile`, `docker-compose.yml`, `requirements.txt`, `pyproject.toml`). (AGENTS §5)

## Code structure rules

1. **No business logic in webhook handlers** — push into services/use-cases under the owning module.
2. **No direct cross-module DB access** — go through the owning module's interface; RLS still enforces `shop_id`.
3. **Pure functions first** — side effects (Redis, DB, Twilio, LLM) at the edges / in services.
4. **One responsibility per file.**
5. **Webhook returns 200 immediately**; real processing enqueued to Celery (SPEC §11).

## Ponytail ladder (apply before writing any new code)

Stop at the first rung that holds — after you understand the problem, not instead:
1. Need to exist at all? (YAGNI) → skip, say so.
2. Already in this codebase? → reuse (grep before writing).
3. Stdlib does it? → use it.
4. Native platform feature? → use it (DB constraint > app code).
5. Already-installed dep? → use it; never add a dep for what a few lines do.
6. One line? → one line.
7. Only then: minimum code that works.

- No unrequested abstractions (interface w/ one impl, factory for one product, config for a value that never changes).
- Deletion over addition. Boring over clever. Fewest files. Shortest working diff (once you understand the problem).
- **Bug fix = root cause.** Grep every caller; fix once where all route through.
- Mark deliberate simplifications with `# ponytail: <ceiling>, <upgrade path>` — harvested into `PONYTAIL-DEBT.md`.
- Non-trivial logic (branch/loop/parser/money/security) leaves **one runnable check** (assert self-check or one `test_*.py`). Trivial one-liners need no test.
- Never simplify away: trust-boundary validation, data-loss-preventing error handling, security, accessibility, anything explicitly requested.

## Karpathy surgical changes

- Touch only what you must. Don't "improve" adjacent code/formatting. Match existing style.
- Remove only orphans YOUR change created; mention pre-existing dead code, don't delete it.
- Every changed line traces to the request.
- State assumptions; if multiple interpretations, present them — don't pick silently.

## Money

- **Never use float for money.** DB columns are `DECIMAL`; in code use `decimal.Decimal` or integer minor units.
- Currency: **AED** (multi-currency deferred — `12-OPEN-QUESTIONS.md`).
- Profit formula and rounding per SPEC §6.

## Error handling

- Errors are typed (custom exception classes per module), never bare strings.
- Never swallow silently; at minimum structured-log.
- LLM errors: retry once, then fallback message (SPEC §11).
- Surface user-facing errors with safe, non-leaky messages.

## Comments & docs

- Comment *why*, not *what*.
- Every public module function/class gets a one-line docstring.
- Update the module `README.md` when behavior changes.

## Testing

- pytest, async via `pytest-asyncio`.
- Tests alongside feature, not "later."
- Minimum: happy path + one failure path per unit.
- Mock external services (Twilio, Supabase, LLM, Telegram) in unit tests; integration tests behind flags.
- LLM tests run against **Moonshot** in CI during testing phase (ADR-004).
- Command: `pytest` (Stage 12 wires CI).

## Git / commits

- Branch per stage/feature.
- Commit message: `stage N: <imperative summary>` (AGENTS §4).
- Never commit `.env`, secrets, build artifacts, `__pycache__`.

## Dependencies

- Before adding a dependency, check `12-OPEN-QUESTIONS.md` / ask; log in an ADR if significant.
- Pin versions in `requirements.txt`.
- Note *why* it was added in the commit/ADR.

## Security conventions

- Every owner/sensitive action writes `audit_logs`.
- Internal API requires `X-Internal-API-Key` (SPEC §1).
- Twilio webhook validates signature first (SPEC §9 step 1).
- Secrets only via env / settings, never in code.

## Anti-patterns to avoid

- Storing prices as floats.
- Querying another shop's data without `shop_id` filter.
- Blocking I/O inside async webhook handlers.
- Hardcoding the LLM provider (must go through `llm/llm_client.py`).
- Exposing `boost_level` / `tags` to customers (SPEC §5).
- Processing a suspended shop's message through the AI (SPEC §2, §9 step 2).
