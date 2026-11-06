# science-bridge handoff format — v0.1

This document defines the shared handoff format between a Claude Code (+ GSD) project and Claude Science. It is the only copy of the format; everything else, including both skills and the validator, follows it.

## Folder layout (inside the project repo)

```
<repo>/
├── science/
│   ├── README.md        map of the project for Claude Science (managed block + project brief)
│   ├── policy.json      access mode, do_not_read, compute hosts, skill_min_version, export rules
│   ├── outbox/          optional: files from OUTSIDE the repo (science-only: also repo files),
│   │                    written only by `bridge.py export`
│   │   └── MANIFEST.json  every exported file: name, sha256, bytes, source, exported_at
│   ├── inbox/           Claude Science → project. One markdown finding per file,
│   │                    plus selftest.json while a self-test waits for `bridge.py verify`
│   ├── archive/         findings already routed into GSD, routing.jsonl, selftest-latest.json
│   └── runs/            outputs of jobs Claude Code ran for Claude Science (one folder per finding slug)
└── .planning/research/questions.md
                         contains "## For Claude Science": the request queue (SQ-NNN items)
```

## policy.json (v3)

```json
{
  "policy_version": 3,
  "access": "repo-readonly",
  "do_not_read": ["holdout/sealed_test_split.yaml"],
  "privacy": "confidential",
  "skill_min_version": "0.4.0",
  "compute": {
    "hpc": {"reach": "claude-code", "notes": "SLURM login node; interactive MFA login"}
  },
  "export": {
    "allowed_sources": [], "allowed_extensions": [".md", ".csv"], "check_command": null, "max_file_mb": 50
  }
}
```

| Key | Meaning |
|-----|---------|
| `access`, `do_not_read` | what Claude Science may read (see below). These are the **only** keys that limit reading. |
| `privacy` | `phi`, `confidential` or `public`. It governs exports, and `phi` requires `export.allowed_sources`. Findings never contain case-level data, whatever the level. |
| `skill_min_version` | the lowest `science-findings` skill version this project works with. An older skill must stop and ask for an update. |
| `compute` | hosts for heavy work. `reach: claude-science` means Claude Science uses its own Compute target of that name. `reach: claude-code` means Claude Science writes an `analysis-proposal` with a `### Run spec` and Claude Code runs it. |
| `export` | rules for `bridge.py export` into `outbox/`. **This block never limits what Claude Science reads.** |

Policies older than v3 have the export keys at the top level. `bridge.py init` upgrades them in place and keeps every value.

## Access modes (`policy.json` → `access`)

| `access` | What Claude Science is granted | What it may read | Where it may write |
|----------|--------------------------------|------------------|--------------------|
| `repo-readonly` (default) | the repo root **read-only**, plus `science/` **read & write** | everything in the repo (code, `.planning/`, git history) except paths matching `do_not_read` | `science/inbox/` only |
| `science-only` | `science/` only | only files listed in `outbox/MANIFEST.json` | `science/inbox/` only |

`do_not_read` is a list of glob patterns, relative to the repo root, that Claude Science must never open, whatever the mode. Example: a sealed pre-registration fold.

## Outbox rules

The outbox holds copies of files from **outside** the repo. `bridge.py export` takes such a file only if its real path matches an absolute `export.allowed_sources` pattern, such as `"~/shared/ajcc/*.docx"`. Its manifest `source` is then that absolute path.
- **`repo-readonly`:** Claude Science reads repo files where they are, so `export` refuses them. Older copies of repo files (a repo-relative `source`) are reported as `redundant` and removed with `bridge.py outbox-clean`. Until then, read the repo file, not its outbox copy.
- **`science-only`:** `export` also copies repo files, matched by relative patterns, and the outbox is all Claude Science may read.
- Every file in `outbox/` (except `MANIFEST.json` and `.gitkeep`) must be listed in `MANIFEST.json` with a matching sha256. Any other file is a violation, and `bridge.py check` fails on it.
- Files enter the outbox only through `bridge.py export`, which applies `policy.json`.
- Claude Science treats the outbox as read-only.

## Request queue (`.planning/research/questions.md` → `## For Claude Science`)

GSD agents or the user record questions for Claude Science here, one per line:

```markdown
- [ ] SQ-001 (2026-11-06, gsd-phase-researcher phase 3): Do AJCC 8 oral-cavity tables have the same merged-label offset? — context: HNC-staging-document/ca_oral_cavity.docx; affects: LBL-01, LBL-06
- [x] SQ-002 (2026-11-05, user): Literature review of LLM TNM staging — answered: science/archive/2026-11-06-llm-staging-literature.md
```

Rules:
- IDs are `SQ-` followed by at least three digits. Each new item takes the next free number.
- Before adding an item, check that the same question isn't already listed.
- Claude Science answers an item with a finding whose frontmatter has `answers: [SQ-001]`. When that finding is archived, `bridge.py archive` ticks the item and appends ` — answered: <archive path>`.
- A finding that covers only part of an item also sets `partial: true`. Archiving then leaves the item open and appends ` — progress: <archive path>`. The finding that completes the item omits `partial`.

## Finding files (inbox)

**Filename:** `YYYY-MM-DD-<slug>.md`, where the slug is lowercase letters, digits and hyphens. Example: `2026-11-06-ajcc-larynx-stage-groups.md`.

**Size:** 200 KB at most per finding.

**Attachments:** a large table or a figure goes in a separate file next to the finding, for example `inbox/2026-11-06-ajcc-larynx-stage-groups.csv`. List it in `sources` as `inbox/<name>`. When the finding is archived, its listed attachments move with it.

**Frontmatter:**

```yaml
---
format: science-finding/v0.1
title: Larynx AJCC 8 stage-grouping table extracted
type: rule-check
created: 2026-11-06
author: claude-science
confidence: medium
sources:
  - HNC-staging-document/ca_larynx.docx
  - https://pubmed.ncbi.nlm.nih.gov/00000000/
contains_case_data: false
affects: [LBL-01, LBL-06]
answers: [SQ-001]
builds_on:
  - science/archive/2026-11-05-ajcc8-larynx-labels-and-stage-groups.md
  - .planning/research/PITFALLS.md
provenance: "Claude Science project 'stagebench', artifact 'larynx_groups.csv' v2"
---
```

| Key | Required | Allowed values |
|-----|----------|----------------|
| `format` | yes | exactly `science-finding/v0.1` |
| `title` | yes | one line, at most 120 characters |
| `type` | yes | `finding`, `literature`, `analysis-proposal`, `rule-check`, `question` |
| `created` | yes | `YYYY-MM-DD` |
| `author` | yes | who wrote it (`claude-science` or a person) |
| `confidence` | yes | `high`, `medium`, `low` |
| `sources` | yes | non-empty list of repo paths, outbox paths and/or URLs |
| `contains_case_data` | yes | must be `false` |
| `affects` | no | list of requirement IDs, phase names or decision names |
| `answers` | no | list of request IDs (`SQ-NNN`) this finding answers |
| `builds_on` | no | list of existing work it builds on: repo paths, archived findings. Expected whenever the duplicate-work check found related work |
| `provenance` | no | a Claude Science artifact/version reference, so numbers can be traced |
| `partial` | no | `true` when the finding answers only part of the items in `answers` (requires `answers`) |
| `compute` | no | for `analysis-proposal` only: the `policy.json` compute host to run on. The body must then contain a `### Run spec` subsection under `## Suggested action` |

What each `type` means:

| Type | Meaning | Default GSD route |
|------|---------|-------------------|
| `finding` | a result from analysing project data, code or documents | note |
| `literature` | a summary of published work | note |
| `rule-check` | a check of domain rules or guidelines | note (flagged if it conflicts with a decision) |
| `analysis-proposal` | an analysis worth implementing in project code | todo |
| `question` | something Claude Science needs answered, or data it would need | note, surfaced to the user |

**Body (required headings, in this order):**

```markdown
## Summary
Two to five sentences. Lead with the conclusion. Say what was already known in the project
(see builds_on) and what is new in this finding.

## Details
Evidence, tables, reasoning. Cite the source (repo path, URL, or artifact) of every number.

## Suggested action
What the project should do, e.g. "add a todo to implement X" or "update requirement LBL-01".
Claude Science suggests; it never edits project code or planning files itself.
```

## Duplicate-work check (required before every task)

Before starting work, Claude Science checks whether the work was already done. It looks in, in this order:

1. `science/archive/` (with `routing.jsonl`) and `science/inbox/`: earlier findings.
2. `.planning/`, especially:
   - `PROJECT.md`, `REQUIREMENTS.md`, `ROADMAP.md`, `STATE.md`;
   - `research/` (including `questions.md`);
   - `phases/*/` (`*-RESEARCH.md`, `*-PLAN.md`, `*-SUMMARY.md`, `*-CONTEXT.md`);
   - `notes/`, `todos/`.
3. The code: is the analysis already implemented, tested, or decided against?
4. `git log` (recent commits): what changed recently.
5. `science/runs/`: results of earlier jobs that Claude Code ran for Claude Science.

Depending on what it finds:
- **Already fully covered:** don't redo it. Write a short `finding` that points to where the work is (in `builds_on`) and what, if anything, is still missing.
- **Partly covered:** build on it, and list it in `builds_on`. The Summary must separate what was known from what is new.

## Run spec (for `compute:` proposals)

```markdown
## Suggested action
Run the bootstrap on hpc and report the CI table.

### Run spec
- run: `sbatch scripts/bootstrap_ci.sbatch` (module load python/3.11)
- inputs: results/run-2026-11-06/metrics.parquet
- outputs: science/runs/2026-11-06-bootstrap-ci/ (ci.csv, log.txt)
- resources: partition=cpu, 1 node, 8 cores, 16G, 30 min
- check: ci.csv has one row per model, lower ≤ estimate ≤ upper
```

Claude Code turns this into a GSD todo. When it is executed, the outputs are committed under `science/runs/<slug>/`. Claude Science reads them there and writes a follow-up finding with `builds_on: [science/runs/<slug>/]`.

## Self-test file (`inbox/selftest.json`)

`scripts/selftest.py` writes it, and `bridge.py verify` checks it against the real repo, then moves it to `archive/selftest-latest.json`. The format is `science-bridge-selftest/v1`, with these fields:
- `skill_version` and `policy`;
- `repo`: `git_head`, `tracked_files` and a probe file's sha256;
- `write_probe_repo_root` (must be `denied`);
- `inbox_write` (must be `ok`);
- `requests_open`;
- `compute` results.

It is not a finding.

## Privacy

- `contains_case_data: false` is mandatory. A finding must never quote patient-level text such as report sentences, identifiers or dates of individual cases.
- Never open paths matching `do_not_read`.
- Under `"access": "science-only"`, the only data Claude Science may use is what is listed in `outbox/MANIFEST.json`.

## Versioning

The format string is versioned (`science-finding/v0.1`). `answers` and `builds_on` are optional additions within v0.1. The validator rejects unknown versions, and a breaking change requires a new version string.
