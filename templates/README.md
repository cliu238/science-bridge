# Science handoff: {{project_name}}

<!-- science-bridge:managed:start -->
<!-- Managed by science-bridge: `/science-bridge:bridge init` rewrites this block. Put your own notes in the Project brief below. -->

This project works with **Claude Science** through this `science/` folder (science-bridge). The rules for Claude Science live in its `science-findings` skill. This page only says where things are.

| What | Where |
|---|---|
| Access mode, `do_not_read`, compute hosts | `science/policy.json`: `access`, `do_not_read`, `compute` |
| What Claude Science may read | `repo-readonly`: the whole repo, read-only, except `do_not_read` paths. The `export` block in `policy.json` only controls `bridge.py export`. It never limits what Claude Science reads. |
| Where Claude Science writes | `science/inbox/` only |
| Open questions for Claude Science | `.planning/research/questions.md` → `## For Claude Science` (SQ-NNN items) |
| Earlier findings | `science/archive/`, with `science/archive/routing.jsonl` |
| The project itself | `.planning/PROJECT.md`, `.planning/ROADMAP.md`, and `.planning/REQUIREMENTS.md` (IDs to cite in `affects`) |
| Results of runs that Claude Code did for Claude Science | `science/runs/<finding-slug>/` |
<!-- science-bridge:managed:end -->

## Project brief

{{brief}}
