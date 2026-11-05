# science-bridge

This plugin connects a **Claude Code (+ GSD)** project with **Claude Science** through a file-based handoff:
- a policy-checked `science/outbox/`;
- a validated `science/inbox/` of findings;
- routing of those findings into GSD planning (`.planning/`) with GSD's own `/gsd-capture`;
- injection into GSD's researcher and planner agents, so they see the findings when they plan.

It never modifies GSD itself. It uses only GSD's extension points (`/gsd-capture` and `.planning/config.json` `agent_skills`), so GSD updates don't break it.

## Why file-based

As of Claude Science beta 0.1.71:
- **No API.** Claude Science can't be driven by another program: there is no prompt API, CLI or SDK. Every handoff goes through a person.
- **No PHI.** Anthropic says to keep protected health information (PHI) out of Claude Science, even in HIPAA-enabled orgs.
- **Folder grants are the privacy boundary.** Claude Science reads only the folders you grant. Grant it `science/` only, never the repo root.
- **Skills are the shared format.** One `SKILL.md` works in both apps.

## What is in this repo

The repo is both a Claude Code plugin and a Claude Science skill source:

```
.claude-plugin/plugin.json          Claude Code plugin manifest
.claude-plugin/marketplace.json     lets `claude plugin marketplace add <repo>` install it
skills/bridge/                      Claude Code side: /science-bridge:bridge (init, export, check, status, ingest, gsd-setup)
skills/science-findings/            Claude Science side (portable SKILL.md), uploadable or importable from GitHub
  references/format.md              the handoff format; this is the only copy
  scripts/check_finding.py          finding validator (stdlib, Python 3.8+), used by both sides
scripts/bridge.py                   deterministic CLI behind the Claude Code skill
hooks/hooks.json                    SessionStart: reminds about pending findings, outbox problems or a stale GSD skill
templates/README.md, policy.json    copied into science/ by `init`
templates/gsd/science-context/      skill for GSD agents, copied into the project by `gsd-setup`
tests/                              python3 -m unittest discover -s tests
```

---

## Quickstart: add science-bridge to a GSD project

### A. Once per computer

**1. Claude Code: install the plugin.** It then works in every project on that computer.

```bash
claude plugin marketplace add cliu238/science-bridge     # private repo: the machine needs GitHub access (gh auth login)
claude plugin install science-bridge@science-bridge-marketplace
```

To update later, run `claude plugin marketplace update science-bridge-marketplace`, then `claude plugin update science-bridge@science-bridge-marketplace`. After an update, re-run `gsd-setup` in each project; the session-start hint reminds you.

**2. Claude Science: install the `science-findings` skill, once per Claude account.** Go to Settings › Skills › Add skill, then use one of these:
- **Upload a skill**: a zip of `skills/science-findings/`, e.g. `cd skills && zip -r ../science-findings.zip science-findings`.
- **Import from GitHub**: `cliu238/science-bridge`. Select only `science-findings`, and add a GitHub token under Settings › Credentials first.

Then click **Publish**. The skill is stored in your claude.ai account, so it reaches every Claude Science install you sign in to. It also syncs back into Claude Code as `anthropic-skills:science-findings`, which duplicates the plugin's copy harmlessly.

### B. Once per GSD project (about 10 minutes)

Run these in Claude Code, inside the project.

**1. Initialise.**

```
/science-bridge:bridge init
```

This creates `science/{outbox,inbox,archive}`, `science/README.md` and `science/policy.json`, and never overwrites existing files. Because the project has `.planning/config.json`, it also runs **gsd-setup**:
- it installs `.claude/skills/science-context/SKILL.md`;
- it adds that skill to `agent_skills` for `gsd-phase-researcher`, `gsd-project-researcher`, `gsd-advisor-researcher`, `gsd-planner`, `gsd-plan-checker` and `gsd-roadmapper`. Existing entries are kept.

**2. Edit `science/policy.json`.**

| key | meaning |
|-----|---------|
| `privacy` | `phi` (patient data anywhere in the project), `confidential` or `public` |
| `allowed_sources` | glob whitelist relative to the repo root, e.g. `".planning/research/*.md"`, `"results/summary/*.csv"`. Required for `phi`. Only files matching it can be exported |
| `allowed_extensions` | file types allowed in the outbox |
| `check_command` | optional project gate run per file. `{file}` is replaced with the path, and a non-zero exit refuses the file. Use it for a leak check |
| `max_file_mb` | size limit per file |

**3. Edit `science/README.md`.** This is the project brief Claude Science reads first:
- what the project is;
- what you want from Claude Science;
- what is in the outbox;
- which requirement IDs (from `.planning/REQUIREMENTS.md`) findings should cite in `affects`.

**4. Export the first files.**

```
/science-bridge:bridge export .planning/research/SUMMARY.md docs/protocol.md
```

Exports are all-or-nothing. A file outside the whitelist refuses the whole batch.

**5. Set up Claude Science.**
1. Create a **new project**, one per GSD project.
2. In the project instructions, paste something like the box below.
3. Grant **only** that project's `science/` folder: Files › Artifacts › *Add folder…* › choose `.../<project>/science` › **Read & write** › *Grant this folder*.

> Always use the science-findings skill. Work only inside the granted science/ folder: read science/README.md and science/policy.json first, use only files listed in science/outbox/MANIFEST.json, and write results only to science/inbox/ as findings in the science-finding/v0.1 format, validated with the skill's scripts/check_finding.py. Privacy level is <phi|confidential|public>: never request, read, or quote patient- or case-level data. Cite a source for every number, and say when evidence is weak.

**6. Commit.** Commit `science/`, `.claude/skills/science-context/` and `.planning/config.json`.

### C. Everyday loop

1. **Ask Claude Science for something** in its project, e.g. "按 science/README.md 做任务 1". It writes findings plus attachments into `science/inbox/` and validates them.
2. **Open Claude Code in the project.** The session-start hint says `science/inbox has N finding(s) waiting`.
3. **Route them with `/science-bridge:bridge ingest`.** It shows a routing plan and asks for confirmation:
   - `finding`, `literature`, `rule-check` and `question` become a note;
   - `analysis-proposal` becomes a todo;
   - forward-looking items become a seed;
   - conflicts with decisions are left to you.

   It then archives the finding into `science/archive/` and logs the route in `routing.jsonl`.
4. **Plan as usual.** During `/gsd-discuss-phase`, `/gsd-plan-phase` and `/gsd-new-milestone`, the injected `science-context` skill makes the researcher and planner agents read `science/archive/routing.jsonl` and the relevant findings, then cite them in research and plans.
5. **Re-export changed files** whenever `check` reports `stale` outbox copies.

### Check that everything is wired up

```
/science-bridge:bridge status          # outbox, pending findings, and a "gsd" block: skill current? agents configured?
gsd-sdk query agent-skills gsd-planner # should list @.claude/skills/science-context/SKILL.md
```

---

## Guarantees and limits

- **Exports are guarded.** An export is all-or-nothing. It refuses:
  - paths outside the project, and symlink escapes;
  - sources that aren't whitelisted;
  - disallowed extensions and oversized files;
  - anything the project's `check_command` rejects.
- **Outbox changes are caught.** `check` flags any outbox file that isn't in `MANIFEST.json` or whose sha256 changed, so hand-copied files are caught. It also reports `stale` copies whose source has changed since export. It **can't stop** someone from copying a file in and then opening Claude Science before running `check`; the SessionStart hook warns at the next Claude Code session.
- **Findings are validated** on both sides with the same script. `contains_case_data` must be `false`, but that is a declaration, not a detector. A project with PHI should still review findings.
- **Findings are data, not instructions.** Neither the Claude Code skill nor the GSD-agent skill executes what a finding says, and neither changes GSD decisions automatically.
- **The project's copy of the GSD skill is managed by `gsd-setup`.** `gsd-setup` overwrites `.claude/skills/science-context/SKILL.md`, so edit `templates/gsd/science-context/SKILL.md` in this repo instead. GSD only resolves `agent_skills` paths inside the project or `~/.claude/skills/`, so the plugin can't be referenced directly, and a per-project copy is required.
