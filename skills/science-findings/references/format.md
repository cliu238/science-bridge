# science-bridge handoff format — v0.1

This document defines the shared handoff format between a Claude Code (+ GSD) project and Claude Science. It is the only copy of the format; everything else, including both skills and the validator, follows it.

## Folder layout (inside the project repo)

```
science/
├── README.md        project brief for Claude Science (written by the project owner)
├── policy.json      machine-readable privacy and export rules
├── outbox/          project → Claude Science. Written only by `bridge.py export`.
│   └── MANIFEST.json  every exported file: name, sha256, bytes, source, exported_at
├── inbox/           Claude Science → project. One markdown finding per file.
└── archive/         findings already routed into GSD, plus routing.jsonl
```

Claude Science is granted the `science/` folder only, never the repo root.

## Outbox rules

- Every file in `outbox/` (except `MANIFEST.json` and `.gitkeep`) must be listed in `MANIFEST.json` with a matching sha256. Any other file is a violation, and `bridge.py check` fails on it.
- Files enter the outbox only through `bridge.py export`, which applies `policy.json`.
- Claude Science treats the outbox as read-only.

## Finding files (inbox)

**Filename:** `YYYY-MM-DD-<slug>.md`, where the slug is lowercase letters, digits and hyphens. Example: `2026-11-06-ajcc-larynx-stage-groups.md`.

**Size:** 200 KB at most per finding.

**Attachments:** a large table or a figure goes in a separate file next to the finding, for example `inbox/2026-11-06-ajcc-larynx-stage-groups.csv`. List it in `sources` as `inbox/<name>`. When the finding is archived, its listed attachments move with it. Attachments follow the same privacy rule as findings.

**Frontmatter (required):**

```yaml
---
format: science-finding/v0.1
title: Larynx AJCC 8 stage-grouping table extracted
type: rule-check
created: 2026-11-06
author: claude-science
confidence: medium
sources:
  - outbox/ca_larynx.docx
  - https://pubmed.ncbi.nlm.nih.gov/00000000/
contains_case_data: false
affects: [LBL-01, LBL-06]
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
| `sources` | yes | non-empty list of outbox paths and/or URLs |
| `contains_case_data` | yes | must be `false` |
| `affects` | no | list of requirement IDs, phase names or decision names |
| `provenance` | no | a Claude Science artifact/version reference, so numbers can be traced |

What each `type` means:

| Type | Meaning | Default GSD route |
|------|---------|-------------------|
| `finding` | a result from analysing outbox data | note |
| `literature` | a summary of published work | note |
| `rule-check` | a check of domain rules or guidelines | note (flagged if it conflicts with a decision) |
| `analysis-proposal` | an analysis worth implementing in project code | todo |
| `question` | something Claude Science needs answered, or data it would need | note, surfaced to the user |

**Body (required headings, in this order):**

```markdown
## Summary
Two to five sentences. Lead with the conclusion.

## Details
Evidence, tables, reasoning. Cite the sources and the outbox file each number comes from.

## Suggested action
What the project should do, e.g. "add a todo to implement X" or "update requirement LBL-01".
Claude Science suggests; it never edits project planning files itself.
```

## Privacy

- `contains_case_data: false` is mandatory. A finding must never quote patient-level text such as report sentences, identifiers or dates of individual cases.
- If an analysis would need case-level data, write a `question` finding that explains what is needed. Don't request or read that data.
- Under `policy.json` `"privacy": "phi"`, the only data Claude Science may use is what is in `outbox/`.

## Versioning

The format string is versioned (`science-finding/v0.1`). The validator rejects unknown versions, and a breaking change requires a new version string.
