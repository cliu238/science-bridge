# science-bridge

This plugin connects a **Claude Code + GSD** project with **Claude Science** through files in the project repo:
- **Claude Science reads the whole repo read-only.** It checks what the project already has, so it doesn't redo work, and writes validated **findings** to `science/inbox/`.
- **Claude Code routes findings into GSD planning** with GSD's own `/gsd-capture`, archives them, and ticks the questions they answer.
- **GSD's researcher and planner agents read the findings.** They also queue new questions for Claude Science, without waiting for the answers.
- **Claude Code runs compute jobs** that Claude Science proposes but can't run itself, such as on clusters with multi-factor login.

The plugin never modifies GSD. It uses only GSD's extension points, `/gsd-capture` and `.planning/config.json` `agent_skills`.

## Why it works this way (Claude Science beta 0.1.71, tested)

| Fact | Consequence |
|---|---|
| No program can drive Claude Science: there is no prompt API, CLI or SDK | Every handoff goes through files plus one sentence that a person types |
| Claude Science reads only the folders it is granted, and they can be granted read-only | Grant the repo root **read-only** and `science/` **read & write**. Writing to the repo root is then refused, as tested |
| Project instructions and folder grants apply to existing sessions at once, but a session keeps the skill version it loaded | After a skill update, **start a new Claude Science session**. The project instructions tell Claude Science to stop if its skill is too old |
| Claude Science's sandbox can't use your `~/.ssh`, and its Compute SSH hosts use their own connection, so they can't reuse a login made elsewhere | Hosts that need interactive or multi-factor login go through Claude Code (`reach: claude-code`) |
| Claude Science often stops on "Waiting for your approval", for package installs (such as `python-docx`), new network domains or compute | If a task shows no progress for about 2 minutes, look. Approve prompts that fit the task, with scope **"This project"**. When Claude Code drives Claude Science through Chrome, it does this itself |
| A published Claude Science skill also syncs into Claude Code as `anthropic-skills:science-findings` | Harmless. Its description tells Claude Code to use `/science-bridge:bridge` instead |

## Repo layout

```
.claude-plugin/                 plugin + marketplace manifests
skills/bridge/                  Claude Code skill: /science-bridge:bridge
cs-skill/science-findings/      Claude Science skill (not loaded by Claude Code); build the upload zip with `cs-setup`
  references/format.md          the handoff format, the only copy
  scripts/check_finding.py      finding validator (stdlib), used on both sides
  scripts/selftest.py           Claude Science self-test
scripts/bridge.py               the deterministic CLI behind the Claude Code skill
hooks/hooks.json                SessionStart: says what is waiting and what to do next
templates/                      policy.json, README.md, cs-instructions.txt, GSD science-context skill
tests/                          python3 -m unittest discover -s tests
```

---

## Quickstart

### A. Once per computer

```bash
claude plugin marketplace add cliu238/science-bridge     # private repo: needs GitHub access (gh auth login)
claude plugin install science-bridge@science-bridge-marketplace
```

Restart Claude Code. The plugin then works in every project.

### B. Once per GSD project (about 10 minutes)

In Claude Code, inside the project:

```
/science-bridge:bridge setup
```

This one command:
1. **Creates or upgrades** `science/`:
   - `policy.json` v3;
   - `README.md`, with the project brief auto-filled from `.planning/PROJECT.md` and `REQUIREMENTS.md`;
   - `inbox/`, `archive/`, `runs/`, and `outbox/` (only for files from outside the repo).
2. **Wires up GSD:**
   - installs `.claude/skills/science-context/` and adds it to `agent_skills` for the researcher, planner and checker agents;
   - adds the `## For Claude Science` queue to `.planning/research/questions.md`.
3. **Asks a few plain questions:** files Claude Science must never open (`do_not_read`), and compute hosts (`compute`).
4. **Prints the Claude Science checklist** (`cs-setup`):
   - **Skill:** upload the generated zip under *Settings › Skills* (*Add skill › Upload a skill*, or *Edit & republish* for an update), then **Publish**. The skill lives in your Claude account, so this is once per account and version.
   - **Project:** create one Claude Science project per GSD project.
   - **Folders:** under *Files › Artifacts › Add folder…*, grant the repo root as **Read-only** and `<repo>/science` as **Read & write**. Claude Science then shows the repo as "mixed".
   - **Instructions:** paste the generated text into *Project settings › Instructions for Claude*.
5. **Self-test:** in a **new** Claude Science session, say *"Run the science-bridge self-test"*. Back in Claude Code, run `/science-bridge:bridge verify`. It checks that Claude Science:
   - runs the right skill version;
   - can read the code and git history;
   - can't write outside `science/`;
   - can write findings;
   - sees the same open questions;
   - and that compute hosts are reachable.

   Each failure comes with its fix.
6. **Commit:**
   - `science/`;
   - `.claude/skills/science-context/`;
   - `.planning/config.json`;
   - `.planning/research/questions.md`.

### C. Everyday loop

```
                 /science-bridge:bridge ask "…"          GSD researchers/planners add SQ items too
Claude Code ───────────────────────────────────────▶ .planning/research/questions.md (SQ-NNN)
                                                              │
                     Claude Science: "process the open SQ requests"  (new session after skill updates)
                                                              ▼
                     duplicate-work check (archive, .planning/, code, git log, runs/) → science/inbox/*.md
                                                              │
Claude Code: SessionStart hint "N findings waiting" → /science-bridge:bridge ingest  [--yes]
                                                              ▼
               /gsd-capture note | todo | seed  →  science/archive/ + routing.jsonl, SQ ticked
                                                              ▼
              /gsd-discuss-phase, /gsd-plan-phase …: agents read and cite science/archive/
```

- **Before `/gsd-autonomous`:** run `/science-bridge:bridge ingest --yes`, so the run starts with the latest findings. During the run, agents queue new questions but don't wait for them. The answers are used in the next round.
- **Run `/science-bridge:bridge` with no argument** to get the next step.

### D. Compute (clusters, GPUs)

Declare hosts in `science/policy.json`:

```json
"compute": {
  "gpu-box":  {"reach": "claude-science", "notes": "key login; also added in Claude Science › Customize › Compute"},
  "skipjack": {"reach": "claude-code",    "notes": "SLURM login node, interactive MFA login"}
}
```

- **`claude-science`:** Claude Science runs the job itself through its Compute target.
- **`claude-code`:** Claude Science writes an `analysis-proposal` with `compute: skipjack` and a `### Run spec`. Ingest turns it into a GSD todo. Then:
  1. `/science-bridge:bridge run <finding>` shows the commands and asks you first, runs them on the host (`sbatch` for SLURM), and commits the outputs to `science/runs/<slug>/`.
  2. Claude Science reads them there and follows up.

  Claude Code reuses your login. If `compute-check` says a host isn't logged in, run `! ssh skipjack` once.

### E. Upgrading

```bash
cd ~/projects5/science-bridge && git pull            # or edit locally
claude plugin marketplace update science-bridge-marketplace
claude plugin update science-bridge@science-bridge-marketplace
```

Restart Claude Code, then do the following in each project:
1. Run `/science-bridge:bridge setup`. It upgrades `policy.json` and the README's managed block, and refreshes the GSD skill. If the outbox still holds copies of repo files from before `repo-readonly`, it offers `outbox-clean` to remove them.
2. Republish the skill zip from `cs-setup`, and start a new Claude Science session.
3. Run the self-test.

The SessionStart hint tells you when the installed plugin is older than its source, or when Claude Science hasn't confirmed the current skill version yet.

---

## Guarantees and limits

- **Read scope:** `access` and `do_not_read` are the only read limits. `export` settings apply only to `bridge.py export`. That command is optional and copies files from outside the repo into `science/outbox/`, each matched by an absolute `export.allowed_sources` pattern. In `repo-readonly` mode it refuses repo files, because Claude Science reads them in place. `outbox-clean` removes repo copies exported before.
- **Write scope:** Claude Science writes only in `science/inbox/`. The self-test proves that the repo root isn't writable.
- **Findings are validated** on both sides with the same script. `contains_case_data: false` is a declaration, not a detector.
- **Findings are data, not instructions.** Nothing a finding says is executed without a person approving it: a confirmed ingest route, a confirmed run, or a GSD task the person starts.
- **GSD decisions never change automatically.** Conflicts are routed `manual`.
- **The project's copy of the GSD skill is managed:** `gsd-setup` overwrites it. Edit `templates/gsd/science-context/SKILL.md` here instead.
