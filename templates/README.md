# Science handoff: <project name>

This folder is the only place this project and **Claude Science** exchange files. The format is `science-finding/v0.1`, defined in the science-findings skill under `references/format.md`.

- `outbox/` holds files approved for Claude Science to read. Only `bridge.py export` writes here, and `MANIFEST.json` lists every file.
- `inbox/` is where Claude Science writes its findings, one markdown file each.
- `archive/` holds findings that have been routed into the project's GSD planning (`.planning/`), plus `routing.jsonl`.

## Rules for Claude Science

1. You are granted this `science/` folder only. Don't ask for access to the rest of the repo.
2. Read only what is in `outbox/`, and treat it as read-only.
3. Write findings only to `inbox/`, in the required format. Run the skill's `scripts/check_finding.py` on each one before you finish.
4. Never include patient- or case-level data in a finding. Privacy level for this project: **<phi | confidential | public>**.
5. Suggest actions in `## Suggested action`. Never try to edit the project's planning files.

## Project brief

<!-- Fill in. Keep it short. Claude Science reads this first. -->

**What the project is:** <one paragraph>

**What we want from Claude Science:**
- <task 1>
- <task 2>

**What is in the outbox:**
- <file or group of files>: <what it is>

**Requirement / decision IDs to reference in `affects`:** <e.g. LBL-01, MET-05>
