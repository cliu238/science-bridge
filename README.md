# science-bridge

**science-bridge connects a software project built with Claude Code + GSD to Claude Science.** The project gets real scientific work, such as literature reviews, guideline and rule checks, statistics and analyses on a cluster. The results land in GSD's plans, where its researchers and planners use them. Nothing is copied by hand, and no work is done twice.

It is a Claude Code plugin plus a Claude Science skill. The two apps never talk directly; they work through a small `science/` folder inside the project repo.

---

## Why this project exists

Claude Code + GSD is good at building software: it plans phases, researches, writes code and tracks decisions in `.planning/`. Claude Science is good at science: literature, domain rules, statistics, data analysis, with its own connectors and compute. A research-software project needs both. Out of the box they don't fit together:

| Problem without a bridge | What science-bridge does |
|---|---|
| **No API.** Claude Science can't be called by another program, so results move by copy and paste. | A file handoff. Claude Science writes **findings** to `science/inbox/`, and Claude Code picks them up. A person only types one sentence in each app. |
| **Duplicated work.** Claude Science doesn't know what the project already researched, decided or built. | Claude Science reads the **whole repo read-only** (code, `.planning/`, git history). Before every task it runs a **duplicate-work check** and builds on what exists, citing it in `builds_on`. |
| **Lost results.** A good answer stays in a chat that the planners never see. | `ingest` routes each finding into GSD (note, todo or seed) with GSD's own `/gsd-capture`, and archives it. The injected `science-context` skill makes GSD's researcher and planner agents read and cite archived findings. |
| **Questions don't travel.** Planning hits a gap, and nobody asks the expert. | A **question queue** (SQ-001, SQ-002 …) in `.planning/research/questions.md`. You or GSD agents add questions; Claude Science answers them; archiving ticks them off, with partial answers kept open. |
| **No cluster access.** Claude Science can't log in to clusters that need interactive multi-factor login. | **Compute handoff.** Claude Science writes a proposal with a run spec, Claude Code runs it on the cluster, and the outputs come back to `science/runs/` for Claude Science to read. |
| **Fragile setup.** Wrong folder grants, a stale skill, a write grant that's too wide: you notice too late. | A **self-test** in Claude Science, plus `verify` in Claude Code, checks every part of the setup and says how to fix each failure. |

---

## Features

**Setup and checks**
- `setup`: one idempotent command creates or upgrades `science/`. It auto-fills the project brief from `.planning/PROJECT.md` and `REQUIREMENTS.md`, migrates old policies with every value kept, and wires up GSD.
- `cs-setup`: prints the one-time Claude Science checklist (skill zip, the two folder grants, the instructions text) for this project.
- **Self-test and `verify`** check:
  - the skill version;
  - repo and git readable;
  - repo root **not** writable;
  - inbox writable;
  - the same open questions on both sides;
  - compute reachable.

  Every failure comes with a fix.
- **Skill versioning.** The project instructions tell Claude Science to stop if its skill is older than the project needs.
- **Session-start hints in Claude Code.** They tell you what is waiting: findings, open questions, a self-test, an outdated plugin or skill, or redundant outbox copies.

**Working with Claude Science**
- **Read-only repo access** (`repo-readonly`), plus a `do_not_read` list for files it must never open, such as sealed test sets.
- **A mandatory duplicate-work check** before every task, across archive, `.planning/`, code, git history and earlier runs.
- **The question queue** (`ask`, `requests`), with partial answers (`partial: true`) that keep an item open with a progress link.
- **A validated finding format** (`science-finding/v0.1`), checked by the same validator on both sides. Large tables travel as attachments.

**GSD integration**
- `ingest` routes findings by type:
  - finding, literature, rule-check and question → **note**;
  - analysis-proposal → **todo**;
  - not for now → **seed**;
  - conflicts with decisions → left to you.
- `ingest --yes` applies the default routes and commits, for example right before `/gsd-autonomous`.
- **The `science-context` skill** is injected into GSD's researcher, planner and checker agents through `agent_skills`. It makes them:
  - read and cite findings;
  - keep finding links in plan tasks;
  - add their own questions to the queue without waiting for answers.
- **GSD itself is never modified.** Only its extension points are used.

**Compute**
- `compute` hosts in `policy.json`, each with `reach: claude-science` (Claude Science uses its own Compute target) or `reach: claude-code` (Claude Code runs the job).
- `run`: Claude Code shows a proposal's run spec and asks you, then runs it (SLURM-aware, login shell, asks before submitting jobs). It commits the outputs and a `RUN.md` to `science/runs/<slug>/`.
- `compute-check` tests that Claude Code can log in to each host without a prompt.

**Safety**
- **Claude Science writes only to `science/inbox/`.** The self-test proves that writing to the repo root is refused.
- **Findings are data, not instructions.** Nothing a finding suggests runs without you: a confirmed route, a confirmed run, or a GSD task you start.
- **GSD decisions never change automatically.**
- **The optional outbox** takes only files from outside the repo, each export policy-checked and recorded with its hash.

---

## How it works

```
 Claude Code + GSD                       science/ (in the repo)                     Claude Science
 ─────────────────                       ──────────────────────                     ──────────────
 /science-bridge:bridge ask "…"  ──▶  .planning/research/questions.md (SQ-NNN)  ──▶  "process the open SQ requests"
 GSD researchers/planners add SQ too                                                  duplicate-work check over the
                                                                                      whole repo (read-only)
 SessionStart hint: "N findings"  ◀──  science/inbox/*.md  (validated findings)  ◀──  writes findings + attachments
 /science-bridge:bridge ingest
   └─▶ /gsd-capture note | todo | seed ──▶ science/archive/ + routing.jsonl, SQ ticked
 /gsd-plan-phase …: agents read and cite science/archive/

 compute proposal (todo) ─▶ /science-bridge:bridge run ─▶ cluster ─▶ science/runs/<slug>/ ──▶ Claude Science reads results,
                                                                                               writes a follow-up finding
```

---

## Requirements

- Claude Code, with [GSD (get-shit-done)](https://github.com/gsd-build/get-shit-done) in the project (`.planning/config.json`). Without GSD, routing is manual.
- Claude Science (tested with beta 0.1.71), signed in to the same Claude account.
- Python 3.8+ (standard library only).
- Optional:
  - Claude in Chrome, so Claude Code can do the Claude Science UI steps for you (see "Let Claude Code drive Claude Science" for the permissions it needs);
  - ssh access to your cluster.

## Install (once per computer)

```bash
claude plugin marketplace add cliu238/science-bridge     # private repo: needs GitHub access (gh auth login)
claude plugin install science-bridge@science-bridge-marketplace
```

Restart Claude Code. The plugin then works in every project.

## Set up a project (once per GSD project, about 10 minutes)

1. **In Claude Code**, inside the project: `/science-bridge:bridge setup`. It:
   - creates or upgrades `science/` and wires up GSD;
   - asks which files Claude Science must never open, and which compute hosts you use;
   - prints the Claude Science checklist.
2. **In Claude Science**, follow the checklist. Claude Code can do these steps through Chrome if you let it.
   - **Skill:** upload the generated zip under *Settings › Skills* (*Add skill › Upload a skill*, or *Edit & republish*), then **Publish**. This is once per Claude account and skill version.
   - **Project:** create one Claude Science project per repo.
   - **Folders:** under *Files › Artifacts › Add folder…*, add the repo root as **Read-only** and `<repo>/science` as **Read & write**.
   - **Instructions:** paste the generated text into *Project settings › Instructions for Claude*.
3. **Self-test:** in a **new** Claude Science session, say *"Run the science-bridge self-test"*. Then run `/science-bridge:bridge verify` in Claude Code, and fix what it reports until every check passes.
4. **Commit:**
   - `science/`;
   - `.claude/skills/science-context/`;
   - `.planning/config.json`;
   - `.planning/research/questions.md`.

## Optional: let Claude Code drive Claude Science (Claude in Chrome)

Claude Science runs as a local web app at `http://localhost:8765`. With **Claude in Chrome**, Claude Code can do the Claude Science steps for you: upload and publish the skill, add the folders, paste the instructions, start sessions, send requests and watch progress. You need to grant these permissions once:

| Where | What to allow |
|---|---|
| Chrome | Install the **Claude in Chrome** extension and sign in with the **same Claude account** as Claude Code. If this is the first install, restart Chrome. |
| Claude in Chrome extension | Site access for **`http://localhost:8765`** (or "on all sites"). Without it, the page is blocked and screenshots fail. |
| Claude Code | Start it with Chrome enabled (`claude --chrome`). The first time it uses the browser tools (navigate, click, type, screenshot, file upload), it asks for permission. Allow them for this work. |
| File upload | The browser tool can upload only files that the session may share. `cs-setup` therefore builds the skill zip **inside the project**, at `science/.cache/science-findings-<version>.zip` (git-ignored). If you build it elsewhere, copy it into the project first. |
| Claude Science | Its own prompts ("Waiting for your approval": package installs, network domains, compute). Claude Code approves those that fit the task, with scope **"This project"**. It asks you about anything else. Pre-approved items can be reviewed or revoked under *Settings › Permissions*. |

Keep in mind:
- **Keep the Chrome window open and not minimised.** Claude Code works in a tab of its own, because a background tab can't be screenshotted.
- **Claude Code never types passwords** or completes multi-factor logins for you. For example, `ssh skipjack` with JHED + MFA must be done by you, with `! ssh skipjack` in Claude Code.
- **Everything works without Chrome too:** do the checklist from `cs-setup` by hand, and type the requests in Claude Science yourself.

## Everyday use: what to say where

| You want to… | Where | Say / run |
|---|---|---|
| Know what to do next | Claude Code | `/science-bridge:bridge` (no argument) |
| Ask Claude Science something | Claude Code | `/science-bridge:bridge ask "…" --affects "REQ-01"` |
| Have Claude Science work the queue | Claude Science | "process the open SQ requests". It also understands requests in Chinese, such as 处理待咨询问题. |
| Give Claude Science a one-off task | Claude Science | Describe the task. It still checks the repo first and writes a finding. |
| Bring the results into GSD | Claude Code | `/science-bridge:bridge ingest`, or `ingest --yes` to accept default routes and commit |
| Run a compute proposal | Claude Code | `/science-bridge:bridge run science/archive/<file>.md` |
| Check the whole setup | Claude Science, then Claude Code | "Run the science-bridge self-test", then `/science-bridge:bridge verify` |
| Plan with the findings | Claude Code | `/gsd-discuss-phase`, `/gsd-plan-phase`, `/gsd-autonomous`, as usual |

### Command reference (`/science-bridge:bridge <command>`)

| Command | What it does |
|---|---|
| `setup` | Create or upgrade `science/` (policy v3, README, folders), wire up GSD, ask about `do_not_read` and compute |
| `cs-setup` | Print the Claude Science checklist, grants and instructions text, and build the skill zip |
| `verify` | Check the Claude Science self-test against the real repo; every failure comes with a fix |
| `ask "<question>"` | Add an SQ item with the next number (refuses duplicates); options `--by`, `--context`, `--affects` |
| `requests [--all]` | List open questions, or all of them |
| `ingest [--yes]` | Route inbox findings into GSD and archive them; `--yes` accepts default routes and commits |
| `run <finding>` | Run a compute proposal on its host, with your OK, and commit the outputs to `science/runs/<slug>/` |
| `status` / `check` | Show what is pending, versions and the GSD wiring / validate the inbox and outbox |
| `compute-check` | Test that Claude Code can log in to each `reach: claude-code` host |
| `gsd-setup` | Re-install the GSD `science-context` skill and `agent_skills` entries (`setup` does this too) |
| `export <paths>` / `outbox-clean` | Copy policy-checked files from **outside** the repo into the outbox / remove old copies of repo files |

## Compute (clusters, GPUs)

Declare the hosts in `science/policy.json`:

```json
"compute": {
  "gpu-box":  {"reach": "claude-science", "notes": "key login; also added in Claude Science › Customize › Compute"},
  "skipjack": {"reach": "claude-code",    "notes": "SLURM login node, interactive MFA login"}
}
```

- **`claude-science`:** Claude Science runs the job through its own Compute target. This needs key-based ssh login.
- **`claude-code`:** Claude Science writes an `analysis-proposal` with `compute: <host>` and a `### Run spec`. Ingest turns it into a GSD todo. Then:
  1. `run` asks you and executes it from Claude Code, which reuses your ssh login. If `compute-check` says you're not logged in, run `! ssh <host>` once.
  2. The outputs and `RUN.md` land in `science/runs/<slug>/`.
  3. Claude Science reads them and writes a follow-up finding.

## Upgrading

```bash
cd ~/projects5/science-bridge && git pull      # or edit locally
claude plugin marketplace update science-bridge-marketplace
claude plugin update science-bridge@science-bridge-marketplace
```

Claude Code runs a cached copy of the plugin, so the `update` step is required even for a local folder. Restart Claude Code, then do this in each project:
1. Run `/science-bridge:bridge setup`.
2. If `cs-setup` shows a new skill version, republish the zip and start a new Claude Science session.
3. Run the self-test again.

The session-start hint tells you when the installed plugin is older than its source.

## Troubleshooting (all seen in real use)

| Symptom | Cause and fix |
|---|---|
| Claude Science sits idle for minutes | It is probably waiting on **"Waiting for your approval"** (package install, network domain, compute). Approve prompts that fit the task, choosing **"This project"** |
| Claude Science doesn't follow the new rules | Old sessions keep the skill version they loaded. Start a **new session** after republishing. Instructions and folder grants apply at once |
| A message to Claude Science vanished | It was sent while Claude Science was still "Reviewing" its last answer. Wait until it is idle, then send again |
| `verify`: repo not readable / repo writable | Add the repo root under *Add folder…* as **Read-only**, not Read & write, and keep `science/` Read & write |
| Claude Science can't reach your cluster | Its sandbox can't use `~/.ssh`, and its Compute hosts can't do interactive or multi-factor login. Use `reach: claude-code` |
| `sinfo: command not found` over ssh | A non-interactive shell has no SLURM or `module` on PATH. `run` uses a login shell (`bash -l`) |
| New plugin behaviour doesn't show up | Run `claude plugin update …` (see Upgrading) and restart Claude Code |
| Claude Code lists `anthropic-skills:science-findings` | The published Claude Science skill syncs into Claude Code. This is harmless, and its description points Claude Code to `/science-bridge:bridge` |

## Guarantees and limits

- **Read scope:** `access` and `do_not_read` are the only limits on what Claude Science reads. The `export` block applies only to the optional outbox.
- **Write scope:** Claude Science writes only in `science/inbox/`. The self-test proves the repo root isn't writable.
- **Validation:** findings are validated on both sides with the same script. `contains_case_data: false` is a declaration, not a detector, so review findings in projects with sensitive data.
- **Findings are data, not instructions,** and GSD decisions never change automatically. Conflicts are routed `manual`.
- **No direct calls:** Claude Science can't be triggered by Claude Code. During `/gsd-autonomous`, questions queue up and are answered in the next round.

## Repo layout and development

```
.claude-plugin/                 plugin + marketplace manifests
skills/bridge/                  Claude Code skill: /science-bridge:bridge
cs-skill/science-findings/      Claude Science skill (not loaded by Claude Code); cs-setup builds its upload zip
  references/format.md          the handoff format: the only copy
  scripts/check_finding.py      finding validator, used on both sides
  scripts/selftest.py           Claude Science self-test
scripts/bridge.py               the deterministic CLI behind the Claude Code skill (stdlib)
hooks/hooks.json                SessionStart hint
templates/                      policy.json, README.md, cs-instructions.txt, GSD science-context skill
tests/                          python3 -m unittest discover -s tests
```

Run the tests with `python3 -m unittest discover -s tests`, and check the plugin with `claude plugin validate .`. The project's copy of the GSD skill (`.claude/skills/science-context/`) is managed by `gsd-setup`, so edit `templates/gsd/science-context/SKILL.md` instead.
