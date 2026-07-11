# 00 — How to Use This Context Layer

This `docs/` folder is the **persistent memory** of the project. It is designed so that **any LLM, at any stage, can understand the full system by reading a handful of files** — without a human re-explaining anything.

## File conventions

- Files are **numbered** (`00-`, `01-`, …). Read in order when onboarding.
- Files in `docs/DECISIONS/` are individual ADRs (Architecture Decision Records).
- Files in `docs/TEMPLATES/` are copy-paste templates for new modules/decisions.
- **Keep files short.** This is a living index, not a novel. Link out to code or ADRs for detail.

## The golden rule

> **If a fact about the project is important enough to act on, it lives in this folder — not only in someone's head or a chat.**

## Onboarding flow (for a new LLM)

```
README → AGENTS → 00-HOW-TO-USE → 07-CURRENT-STATE → 06-ROADMAP
   → 03-ARCHITECTURE → 05-CONVENTIONS → (module doc) → start work
```

## Maintenance flow (for the working LLM)

```
do work → update 07-CURRENT-STATE → update 08-FILE-MAP (if files changed)
   → write module doc (if new module) → log ADR (if decision made)
   → update .context/HANDOFF.md → commit
```

## Index

| # | File | What it answers |
|---|------|-----------------|
| 00 | HOW-TO-USE | this |
| 01 | VISION | why does this exist? |
| 02 | SCOPE | what's in/out of scope? |
| 03 | ARCHITECTURE | how is it built? |
| 04 | TECH-DECISIONS | what did we choose & why? |
| 05 | CONVENTIONS | how do we write code? |
| 06 | ROADMAP | what's the plan? |
| 07 | CURRENT-STATE | **where are we right now?** |
| 08 | FILE-MAP | what lives where? |
| 09 | GLOSSARY | what do terms mean? |
| 10 | DATA-MODEL | what are the entities? |
| 11 | API-CONTRACTS | what are the interfaces? |
| 12 | OPEN-QUESTIONS | what's unresolved? |
| 13 | ENGINEERING-ETHOS | caveman + karpathy + ponytail rules (MANDATORY) |

Plus: `PONYTAIL-DEBT.md` (root) — ledger of `ponytail:` shortcuts.
