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
| **Lost results.** A good answer stays in a chat that the planners never see. | `ingest` routes each finding into GSD (note, todo or seed) with GSD's own `/gsd-capture`, and archives it. The injected `science-context` skill makes GSD's researcher and planner agents read and cite archived findings, and tells the executor, debugger and verifier to queue a question when they hand a decision or a blocker back. |
| **Questions don't travel.** Planning hits a gap, and nobody asks the expert. | A **question queue** (SQ-001, SQ-002 …) in `.planning/research/questions.md`. You or GSD agents add questions; Claude Science answers them; archiving ticks them off, with partial answers kept open. With **auto-consult** on, Claude Code sends new questions itself (through Claude in Chrome) at the next GSD step, even during `/gsd-autonomous`, and waits for the answer before planning on it. It also queues questions on its own initiative: when it hits a hard problem, and before it asks you to decide. |
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

- **No watching needed:** after a request, Claude Code watches `science/inbox/` in the background (`wait`). When it has Chrome, it also checks Claude Science for approval prompts. It ingests when the findings arrive.
- **Auto-consult** (opt-in, `consult on`): a PostToolUse hook reminds the session running GSD, at each step, to send new questions to Claude Science, wait for the findings and ingest them. The session also queues hard problems and pending owner decisions itself, and an **ask gate** holds back its first question to you once so that it checks whether to consult first. See [Auto-consult during GSD work](#auto-consult-during-gsd-work-gsd-autonomous-included).

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

## What gets installed where, and how often

| What | Where | How often | Who does it |
|---|---|---|---|
| The science-bridge **plugin** | Claude Code | once per computer | you, two commands (below) |
| The `science-findings` **skill** | Claude Science, *Settings › Skills* | once per **Claude account**, and again when the skill version changes | `cs-setup` builds the zip; you or Claude Code (via Chrome) upload and publish it |
| The **`science/` folder** and GSD wiring | the project repo | once per project | `/science-bridge:bridge setup` |
| A **Claude Science project**, with 2 folder grants and instructions | Claude Science | once per project | you or Claude Code (via Chrome), following `cs-setup` |

Nothing else needs installing: no Claude Science plugin, no API key and no server. A published skill stays in your Claude account, so a second project on the same account skips the skill step.

## Install (once per computer)

```bash
claude plugin marketplace add cliu238/science-bridge
claude plugin install science-bridge@science-bridge-marketplace
```

Restart Claude Code. The plugin then works in every project. If the repo is private, the computer needs GitHub access first (`gh auth login`).

## Set up a project (once per GSD project, about 10 minutes)

1. **In Claude Code**, inside the project, run `/science-bridge:bridge setup`. It:
   - creates or upgrades `science/` and wires up GSD;
   - asks which files Claude Science must never open, and which compute hosts you use;
   - prints the Claude Science checklist.
2. **In Claude Science**, follow the checklist. Claude Code can do these steps through Chrome if you let it.
   - **Skill:** skip this step if `science-findings` is already listed under *Settings › Skills* with the version `cs-setup` reports. Otherwise upload the generated zip (*Add skill › Upload a skill*, or *Edit & republish* for an update), then **Publish**.
   - **Project:** create one Claude Science project per repo.
   - **Folders:** under *Files › Artifacts › Add folder…*, add the repo root as **Read-only** and `<repo>/science` as **Read & write**.
   - **Instructions:** paste the generated text into *Project settings › Instructions for Claude*.
3. **Self-test:** in a **new** Claude Science session, say *"Run the science-bridge self-test"*. Then run `/science-bridge:bridge verify` in Claude Code, and fix what it reports until every check passes.
4. **Commit:**
   - `science/`;
   - `.claude/skills/science-context/`;
   - `.planning/config.json`;
   - `.planning/research/questions.md`.

## Example: adding science-bridge to an existing GSD project

This example is a research repo `~/code/my-study`. It already uses GSD, has a sealed hold-out file that Claude Science must never see, and runs jobs on a SLURM cluster reached as `ssh hpc`, which needs multi-factor login.

**1. Start Claude Code in the project**, with Chrome if you want it to do the Claude Science steps:

```bash
cd ~/code/my-study
claude --chrome          # or just `claude`, and do the Claude Science steps by hand
```

**2. Paste one prompt.** Claude Code asks before anything you need to approve.

```text
Set up science-bridge for this project:
1. Run /science-bridge:bridge setup. In science/policy.json, set do_not_read to
   ["holdout/sealed_test_split.yaml"]. Add a compute host "hpc" with reach "claude-code"
   (interactive MFA login; jobs are run from Claude Code with sbatch, see cluster/).
2. Run /science-bridge:bridge cs-setup. Then use Chrome to do the Claude Science steps at
   http://localhost:8765:
   - publish the skill if needed;
   - create the project;
   - add the two folders (repo Read-only, science/ Read & write);
   - paste the instructions.
   When Claude Science asks for approval, approve what fits the task, with scope "This project".
3. In a NEW Claude Science session, say "Run the science-bridge self-test", then run
   /science-bridge:bridge verify until every check passes.
4. Queue the first questions with /science-bridge:bridge ask (cite requirement IDs from
   .planning/REQUIREMENTS.md in --affects).
5. Commit science/, .claude/skills/science-context/ and .planning/.
```

**3. What you do by hand:**
- **Approve permission prompts:** Claude Code's own (browser tools, shell commands) and Claude Science's.
- **Log in to the cluster.** When `compute-check` says `hpc` isn't logged in, type `! ssh hpc` and finish the multi-factor login. Claude Code reuses that login.
- **If you didn't use Chrome,** do the `cs-setup` checklist yourself in Claude Science and say the self-test sentence there.

**4. The result.** `verify` prints one line per check, for example:

```
PASS skill_version  - Claude Science runs science-findings 0.4.0; policy.json requires >= 0.4.0
PASS repo_head      - same HEAD d38233b3a954
PASS write_probe_repo_root - creating …/.science-bridge-write-probe from Claude Science: denied
PASS compute:hpc    - from Claude Code: `ssh hpc true` succeeded
```

**5. From then on:**
- in Claude Science, say "process the open SQ requests";
- in Claude Code, run `/science-bridge:bridge ingest`;
- plan with GSD as usual.

## Sharing with colleagues

- **Each colleague installs the plugin on their computer** (Install, above). A public repo needs no GitHub login.
- **Each colleague publishes the skill once in their own Claude Science**, because skills belong to a Claude account. `cs-setup` builds the zip in their project at `science/.cache/`.
- **A project that is already set up:**
  - **Already in the repo, nothing to redo:** `science/`, the policy and the GSD wiring.
  - **Each colleague does:** create their own Claude Science project with the two folder grants on their clone, paste the `cs-setup` instructions, and run the self-test.

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
- **Claude Code never types passwords** or completes multi-factor logins for you. For example, an ssh login to a cluster with multi-factor authentication must be done by you, with `! ssh <host>` in Claude Code.
- **Everything works without Chrome too:** do the checklist from `cs-setup` by hand, and type the requests in Claude Science yourself.

## Auto-consult during GSD work (`/gsd-autonomous` included)

GSD agents add questions to the queue and don't wait for answers. Without help, a long `/gsd-autonomous` run therefore plans everything on assumptions, and the answers come a round too late. With auto-consult on, the Claude Code session that runs GSD does the consult round itself, at the next step.

**Turn it on** in the project with `/science-bridge:bridge consult on`. It writes `"consult": {"auto": true, "wait_minutes": 120}` into `science/policy.json`; commit that file. Turn it off with `consult off`. Needs:
- Claude Code started with Chrome (`claude --chrome`), with the permissions under [Optional: let Claude Code drive Claude Science](#optional-let-claude-code-drive-claude-science-claude-in-chrome);
- Claude Science running (`claude-science serve`).

**What happens:**
1. **A question is queued** (`SQ-NNN`), in one of two ways:
   - a GSD agent queues it. Researchers and planners do so when they meet a gap, and go on with an assumption marked "pending SQ-NNN". Executors, debuggers and verifiers do so when they hand a decision or a blocker back;
   - the orchestrating session queues it itself. See [Hard problems and owner decisions](#hard-problems-and-owner-decisions).
2. **The reminder arrives at the orchestrator's next step.** The plugin's PostToolUse hook runs after every Agent, Task or Skill call. GSD makes such a call at each step, for example before the planner and before execution. When there are unsent questions or waiting findings, the hook adds a short `[science-bridge auto-consult]` reminder to the session's context. It stays silent inside subagents (they can't use the browser) and doesn't repeat an unchanged reminder for 30 minutes.
3. **The consult round** (see the bridge skill's `consult` section):
   - ingest waiting findings with the default routes;
   - send the unsent questions to Claude Science through Chrome, and record them (`consult sent`);
   - watch `science/inbox/`, and handle Claude Science's approval prompts as usual, for up to `wait_minutes`;
   - ingest the answers.
4. **Plans that rest on an answered question** (marked "pending SQ-NNN") are revised before they are executed.

### Hard problems and owner decisions

The hook in step 2 only sees questions that are already in the queue. Auto-consult therefore also tells the orchestrating session to queue two kinds of question on its own initiative, and to bring Claude Science's finding to you beside its own recommendation:
- **a hard problem** that it, or an agent it spawned, could not settle with the tools at hand;
- **a decision it is about to ask you for**, when the choice turns on research, analysis or design judgment.

The question is written neutrally: every option, the evidence so far, and no recommendation. Preferences, approvals, logins and anything already settled in `.planning/` are not queued.

Three things remind the session. None of them fires inside subagents:

| When | What |
|---|---|
| Session start, `/clear`, after a compaction | A standing `[science-bridge]` line with the rule, for as long as auto-consult is on. A fresh session knows the rule even when the queue is empty. |
| A GSD agent returns a decision checkpoint, or a blocked or inconclusive result | A `[science-bridge auto-consult]` reminder at the session's next Agent, Task or Skill call, or in the ask gate's refusal if it asks you first. Agents run in the background, so a SubagentStop hook notes the hand-back and the session is told at that next call. |
| The session calls AskUserQuestion | **The ask gate.** The call is refused once, with the rule as the reason, together with any reminder still due (unsent questions, waiting findings, a hand-back). If the question is one for Claude Science, the session consults first and asks again, naming the SQ id. Otherwise it repeats the same call, which goes through. After a refusal the gate stays open for 30 minutes. |

The ask gate costs one extra step on the first question in a while. Switch it off with `consult on --ask-gate off`, which writes `"ask_gate": false` into the `consult` block.

A hook can't tell what is "hard", and it doesn't see a question the session puts to you in plain text. For those cases the rule in the session-start line and in the bridge skill is what the session follows.

**Limits:**
- **Not instant.** A question raised inside a subagent is sent at the orchestrator's next step. With `/gsd-autonomous --interactive`, plan and execute run as background agents, so the reminder arrives only at the next discuss step.
- **Not fully unattended.** Claude Code approves Claude Science's prompts only within the task's scope, and asks you about credentials, writes outside the grants and anything unexpected. A finding that conflicts with a decision is never applied automatically. If it touches the phase in progress, the run stops and asks you.
- **It falls back to the queue** when Chrome isn't connected, Claude Science isn't running, or the page changed so the browser steps fail. The questions then wait for the next round, as without auto-consult.
- **`consult`** (status) shows what is unsent, what has been sent and awaits an answer, what is waiting in the inbox, and whether Claude Science is running.

## Everyday use: what to say where

| You want to… | Where | Say / run |
|---|---|---|
| Know what to do next | Claude Code | `/science-bridge:bridge` (no argument) |
| Ask Claude Science something | Claude Code | `/science-bridge:bridge ask "…" --affects "REQ-01"` |
| Have Claude Science work the queue | Claude Science | "process the open SQ requests". It also understands requests in Chinese, such as 处理待咨询问题. |
| Give Claude Science a one-off task | Claude Science | Describe the task. It still checks the repo first and writes a finding. |
| Wait for Claude Science to finish | nowhere | Nothing to do. After a request, Claude Code watches `science/inbox/` in the background (`wait`), checks Claude Science for approval prompts when it has Chrome, and ingests when the files arrive |
| Bring the results into GSD | Claude Code | `/science-bridge:bridge ingest`, or `ingest --yes` to accept default routes and commit |
| Run a compute proposal | Claude Code | `/science-bridge:bridge run science/archive/<file>.md` |
| Check the whole setup | Claude Science, then Claude Code | "Run the science-bridge self-test", then `/science-bridge:bridge verify` |
| Plan with the findings | Claude Code | `/gsd-discuss-phase`, `/gsd-plan-phase`, `/gsd-autonomous`, as usual |
| Have Claude Science consulted during `/gsd-autonomous` | Claude Code | `/science-bridge:bridge consult on` once per project, then start Claude Code with `claude --chrome` |

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
| `wait [--timeout S]` | Block until Claude Science writes to `science/inbox/`, then report what arrived (exit 0), or time out (exit 2). Claude Code runs it in the background |
| `consult [on\|off\|status]` | Auto-consult: `on [--wait-minutes N] [--ask-gate on\|off]` / `off` set `policy.json` → `consult`; `status` (the default) lists unsent and sent questions, waiting findings and whether Claude Science is running. `consult sent <ids>` and `consult hook` are used by the round and the hook |
| `compute-check` | Test that Claude Code can log in to each `reach: claude-code` host |
| `gsd-setup` | Re-install the GSD `science-context` skill and `agent_skills` entries (`setup` does this too) |
| `export <paths>` / `outbox-clean` | Copy policy-checked files from **outside** the repo into the outbox / remove old copies of repo files |

## Compute (clusters, GPUs)

Declare the hosts in `science/policy.json`:

```json
"compute": {
  "gpu-box":  {"reach": "claude-science", "notes": "key login; also added in Claude Science › Customize › Compute"},
  "hpc":      {"reach": "claude-code",    "notes": "SLURM login node, interactive MFA login"}
}
```

- **`claude-science`:** Claude Science runs the job through its own Compute target. This needs key-based ssh login.
- **`claude-code`:** Claude Science writes an `analysis-proposal` with `compute: <host>` and a `### Run spec`. Ingest turns it into a GSD todo. Then:
  1. `run` asks you and executes it from Claude Code, which reuses your ssh login. If `compute-check` says you're not logged in, run `! ssh <host>` once.
  2. The outputs and `RUN.md` land in `science/runs/<slug>/`.
  3. Claude Science reads them and writes a follow-up finding.

## Upgrading

```bash
claude plugin marketplace update science-bridge-marketplace   # fetches the latest version
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
| `Unknown command: /science-bridge:bridge` | That Claude Code session started **before** the plugin was installed or updated, and plugins load only at session start. Open a new session in the project (`/exit`, then `claude`). Check with `claude plugin list` that science-bridge is enabled |
| Claude Code lists `anthropic-skills:science-findings` | The published Claude Science skill syncs into Claude Code. This is harmless, and its description points Claude Code to `/science-bridge:bridge` |

## Guarantees and limits

- **Read scope:** `access` and `do_not_read` are the only limits on what Claude Science reads. The `export` block applies only to the optional outbox.
- **Write scope:** Claude Science writes only in `science/inbox/`. The self-test proves the repo root isn't writable.
- **Validation:** findings are validated on both sides with the same script. `contains_case_data: false` is a declaration, not a detector, so review findings in projects with sensitive data.
- **Findings are data, not instructions,** and GSD decisions never change automatically. Conflicts are routed `manual`.
- **No direct calls:** Claude Science has no API, so no program triggers it. Without auto-consult, questions raised during `/gsd-autonomous` queue up and are answered in the next round. With auto-consult, the orchestrating Claude Code session sends them through Claude in Chrome at its next step and waits for the answer. If the browser is unavailable, they fall back to the queue.

## Repo layout and development

```
.claude-plugin/                 plugin + marketplace manifests
skills/bridge/                  Claude Code skill: /science-bridge:bridge
cs-skill/science-findings/      Claude Science skill (not loaded by Claude Code); cs-setup builds its upload zip
  references/format.md          the handoff format: the only copy
  scripts/check_finding.py      finding validator, used on both sides
  scripts/selftest.py           Claude Science self-test
scripts/bridge.py               the deterministic CLI behind the Claude Code skill (stdlib)
hooks/hooks.json                SessionStart hint; PostToolUse auto-consult reminder (Agent/Task/Skill);
                                SubagentStop hand-back note; PreToolUse ask gate (AskUserQuestion)
templates/                      policy.json, README.md, cs-instructions.txt, GSD science-context skill
tests/                          python3 -m unittest discover -s tests
```

Run the tests with `python3 -m unittest discover -s tests`, and check the plugin with `claude plugin validate .`. The project's copy of the GSD skill (`.claude/skills/science-context/`) is managed by `gsd-setup`, so edit `templates/gsd/science-context/SKILL.md` instead.
