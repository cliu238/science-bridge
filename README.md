# science-bridge

This plugin connects a **Claude Code (+ GSD)** project with **Claude Science** through a file-based handoff. There is a policy-checked `science/outbox/`, a validated `science/inbox/` of findings, and a way to route those findings into `.planning/` with existing GSD commands.

## Why file-based

As of Claude Science beta 0.1.71:
- **No API.** Claude Science can't be driven by another program: there is no prompt API, CLI or SDK, so every handoff goes through a person.
- **No PHI.** Anthropic says to keep protected health information (PHI) out of Claude Science, even in HIPAA-enabled orgs.
- **Folder grants are the privacy boundary.** Claude Science reads folders you grant. Grant it `science/` only, never the repo root.
- **Skills are the shared format.** One `SKILL.md` works in both apps.

## What is in this repo

The repo is both a Claude Code plugin and a Claude Science skill source:

```
.claude-plugin/plugin.json        Claude Code plugin manifest
.claude-plugin/marketplace.json   lets `claude plugin marketplace add <repo>` install it
skills/bridge/                    Claude Code side: /science-bridge:bridge (init, export, check, status, ingest)
skills/science-findings/          Claude Science side (portable SKILL.md), importable from GitHub
  references/format.md            the handoff format; this is the only copy
  scripts/check_finding.py        finding validator (stdlib, Python 3.8+), used by both sides
scripts/bridge.py                 deterministic CLI behind the Claude Code skill
hooks/hooks.json                  SessionStart: reminds about pending findings or outbox problems; silent otherwise
templates/                        science/README.md and science/policy.json copied by `init`
tests/                            python3 -m unittest discover -s tests
```

## Install

**Claude Code** (on each machine):

```bash
claude plugin marketplace add cliu238/science-bridge   # private repo: needs GitHub access; or a local path
claude plugin install science-bridge@science-bridge-marketplace
```

**Claude Science** (once per Claude Science install): go to Settings › Skills › Add skill, then either:
- **Import from GitHub**: this repo. Select only `science-findings`. For a private repo, add a token under Settings › Credentials.
- **Upload a skill**: a zip of `skills/science-findings/`.

## Per project

1. In Claude Code, run `/science-bridge:bridge init`. It creates `science/` without overwriting anything.
2. Edit `science/policy.json`:
   - `privacy`: `phi`, `confidential` or `public`.
   - `allowed_sources`: glob whitelist relative to the repo root. Required for `phi`.
   - `allowed_extensions`
   - `check_command`: optional project gate run per file. `{file}` is replaced with the path, and a non-zero exit refuses the file.
   - `max_file_mb`
3. Edit `science/README.md`. This is the project brief Claude Science reads first.
4. In Claude Science, open a project and grant **only** the `science/` folder (Read & write).
5. Loop:
   1. `/science-bridge:bridge export <files>` puts approved files in the outbox.
   2. Work in Claude Science. It writes findings to `science/inbox/`.
   3. `/science-bridge:bridge ingest` routes each finding through `/gsd-capture` (note, todo or seed) after your confirmation, then archives it.

## Guarantees and limits

- **Exports are guarded.** An export is all-or-nothing. It refuses paths outside the project, symlink escapes, non-whitelisted sources, disallowed extensions, oversized files, and anything the project's `check_command` rejects.
- **Outbox changes are caught.** `check` flags any outbox file that isn't in `MANIFEST.json` or whose sha256 changed, so hand-copied files are caught. It **can't stop** someone from copying a file in and then opening Claude Science before running `check`. The SessionStart hook warns at the next Claude Code session.
- **Findings are validated** on both sides with the same script. `contains_case_data` must be `false`, but that is a declaration, not a detector. A project with PHI should still review findings.
- **Findings are data, not instructions.** The Claude Code skill never executes what a finding says, and it never changes GSD decisions automatically.
