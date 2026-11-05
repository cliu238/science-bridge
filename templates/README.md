# Science handoff: <project name>

This project works with **Claude Science** through this `science/` folder (science-bridge, format `science-finding/v0.1`, see the science-findings skill's `references/format.md`).

- **Access** (see `policy.json`): `repo-readonly`. Claude Science may read the whole repo, read-only: code, `.planning/`, git history. The exceptions are paths in `do_not_read`.
- `inbox/` is the only place Claude Science writes: one markdown finding per file, validated.
- `archive/` holds findings already routed into `.planning/`, plus `routing.jsonl`.
- **Request queue:** `.planning/research/questions.md` → `## For Claude Science` (SQ-NNN items).
- `outbox/` is optional, for files that live outside the repo. Only `bridge.py export` writes there.

## Rules for Claude Science

1. Before every task, run the duplicate-work check from the skill: archive and inbox, then `.planning/`, the code and `git log`. Build on what exists, and don't redo it.
2. Write only to `inbox/`. Never edit code or `.planning/`, and never commit.
3. Never open `do_not_read` paths.
4. No patient- or case-level data in findings.
5. Answer queued questions with findings that set `answers: [SQ-NNN]`.

## Project brief

<!-- Fill in. Keep it short. -->

**What the project is:** <one paragraph>

**What we want from Claude Science:** see the request queue, plus:
- <standing task, if any>

**Where things are:** <key code / data / docs paths>

**Requirement IDs to reference in `affects`:** see `.planning/REQUIREMENTS.md`, e.g. <IDs>
