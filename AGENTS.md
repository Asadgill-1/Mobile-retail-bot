# AGENTS.md — Handoff Protocol

> This file is the **contract** between the project and any AI/LLM that works on it.
> Every LLM that touches this repo MUST read this file first and follow it.

---

## 1. Read order (mandatory)

When you begin work on this project, read these files **in this order** before writing any code:

1. `README.md` — one-paragraph overview
2. `AGENTS.md` ← you are here
3. `docs/00-HOW-TO-USE.md` — how the context layer works
4. `docs/07-CURRENT-STATE.md` — **where the project is RIGHT NOW** (most important)
5. `docs/06-ROADMAP.md` — what's planned next
6. `docs/03-ARCHITECTURE.md` — how the system fits together
7. `docs/05-CONVENTIONS.md` — code/style rules you MUST follow
8. `docs/13-ENGINEERING-ETHOS.md` — **engineering ethos: caveman + karpathy + ponytail (MANDATORY)**
9. The doc for the specific module you're working on (see `docs/08-FILE-MAP.md`)

Read others (`01-VISION`, `04-TECH-DECISIONS`, `09-GLOSSARY`, `10-DATA-MODEL`, `11-API-CONTRACTS`) as needed.

## 2. Before you write code

- [ ] Confirm which stage/task you're on from `CURRENT-STATE` / `ROADMAP`.
- [ ] Check `docs/12-OPEN-QUESTIONS.md` — don't guess on items listed there.
- [ ] Follow `docs/05-CONVENTIONS.md` exactly. Match existing code style.
- [ ] If the task is ambiguous, **ask** rather than assume. Log the question in `12-OPEN-QUESTIONS.md`.

## 3. While you work

- **Keep `docs/07-CURRENT-STATE.md` accurate.** Update it as you complete steps. This is the single source of truth for "where are we."
- **Update `docs/08-FILE-MAP.md`** whenever you add/remove/move a file.
- **Write a module doc** (copy `docs/TEMPLATES/MODULE-DOC.md`) for any new top-level module.
- **Log non-trivial decisions** as an ADR in `docs/DECISIONS/` and add a one-line entry to `04-TECH-DECISIONS.md`.
- **Follow `docs/13-ENGINEERING-ETHOS.md`** (caveman prose, karpathy surgical/simplicity, ponytail lazy ladder). Mark every deliberate shortcut with a `ponytail:` comment naming ceiling + upgrade path.

## 4. When you finish a stage (handoff)

Before handing off to the next LLM, you MUST:

1. Update `docs/07-CURRENT-STATE.md`:
   - Move completed items to "Done"
   - Set the new "Next up" task
   - Bump the stage number and date
2. Update `.context/HANDOFF.md` — the 1-page resume brief.
3. Update `docs/08-FILE-MAP.md` if files changed.
4. **Run a `ponytail-review` pass on the stage's diff** (over-engineering only; one line per finding). Apply the cuts that are clearly safe; leave the rest noted.
5. **Refresh `PONYTAIL-DEBT.md`** by re-harvesting `ponytail:` markers (`grep -rn "ponytail:" src/ tests/ migrations/`).
6. Commit with a message referencing the stage, e.g. `stage 3: inventory module implemented`.
7. Note anything the next LLM should **not** redo (see "Anti-patterns / pitfalls" in `CURRENT-STATE`).

## 5. Rules of engagement

- ✅ **Prefer the existing pattern.** If something is done a certain way already, do it that way unless a decision says otherwise.
- ✅ **Small, verifiable steps.** Each handoff should leave the project in a working state.
- ✅ **Document as you go**, not at the end.
- ✅ **Create required folders as you go.** When building the system further, create whatever folders the work needs (configs, migrations, scripts, public assets, locales, etc.) — organized by purpose, **never** dump loose files at the repo root. Add every new folder to `docs/08-FILE-MAP.md`.
- ❌ **Never delete** files in `docs/` or `.context/` without explicit instruction.
- ❌ **Never change** the tech stack or core architecture without an ADR.
- ❌ **Never assume** a decision was made — if it's not in `04-TECH-DECISIONS.md`, it wasn't.
- ❌ **Never silently skip** a failing test or a TODO; surface it in `CURRENT-STATE`.
- ❌ **Never leave files at the repo root** when they belong in a purpose-built folder (except `README.md`, `AGENTS.md`, and standard config files like `.gitignore`, `.env.example`, package manifests).

## 6. Tech stack

> **See `docs/04-TECH-DECISIONS.md` for the authoritative list.** This section is a quick reference only.

- Language/runtime: **[FILL IN]**
- Backend framework: **[FILL IN]**
- Frontend: **[FILL IN]**
- Database: **[FILL IN]**
- (Update as decisions are made.)

## 7. How to ask the human

If you hit a blocker that needs a human decision:
1. Add it to `docs/12-OPEN-QUESTIONS.md` with context.
2. State clearly in your final message: "Blocked — needs human decision: [link to question]."
