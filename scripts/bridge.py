#!/usr/bin/env python3
"""science-bridge CLI, the Claude Code side of the Claude Code <-> Claude Science handoff.

Standard library only, Python 3.8+. The handoff format is defined in
cs-skill/science-findings/references/format.md.

Commands:
  init [--no-gsd]                   set up or upgrade science/ (idempotent, never deletes user
                                    content): create missing {outbox,inbox,archive,runs}/,
                                    policy.json and README.md; migrate a v1/v2 policy.json to v3
                                    keeping every value; raise skill_min_version to the bundled
                                    skill version; refresh README.md's managed block (a legacy
                                    README is rebuilt around its Project brief, old copy kept)
  cs-setup [--out ZIP]              everything for the one-time Claude Science setup of this
                                    project: skill zip, folder grants, project instructions and
                                    an ordered checklist
  skill-zip [--out ZIP]             build the Claude Science skill zip (top folder
                                    science-findings/); default <tmp>/science-bridge/
  verify [--max-age-hours N]        check science/inbox/selftest.json (written by the Claude
                                    Science self-test) against this repo and policy.json; exit 1
                                    on any failed check; on success move it to
                                    science/archive/selftest-latest.json
  compute-check                     ssh check of every policy.json compute host with reach
                                    "claude-code"; exit 1 if one is unreachable
  export PATH [PATH ...]            copy approved files into science/outbox/ and record them in
                                    MANIFEST.json; refuses anything policy.json "export" does not
                                    allow. A file outside the repo needs an absolute
                                    export.allowed_sources pattern; under access "repo-readonly"
                                    repo files are refused (Claude Science reads them in place)
  outbox-clean [--yes]              repo-readonly only: list the outbox copies of repo files
                                    (exported before repo-readonly); with --yes delete them from
                                    science/outbox/ and MANIFEST.json
  check                             verify outbox integrity against MANIFEST.json and validate
                                    inbox findings; exit 1 on any problem (old copies listed in
                                    outbox.redundant are not a problem)
  status                            versions, policy summary, pending inbox findings with
                                    validation results, self-test state (JSON)
  archive FILE --route R --ref TXT  move a routed finding (and its inbox attachments) to
                                    science/archive/ and append to archive/routing.jsonl; ticks
                                    the SQ items in `answers` (or, with `partial: true`, appends a
                                    progress note to them instead)
  gsd-setup [--agents a,b,...]      GSD integration: install the science-context skill at
                                    .claude/skills/science-context/ and add it to
                                    .planning/config.json agent_skills for the researcher, planner,
                                    checker, executor, debugger and verifier agents (merge only;
                                    existing entries are kept). `init` runs this automatically
                                    when .planning/config.json exists.
  requests [--all]                  list the request queue (SQ-NNN items in
                                    .planning/research/questions.md "## For Claude Science")
  ask TEXT [--by WHO] [--context P] [--affects IDS]
                                    append a new SQ-NNN request with the next free number
  wait [--timeout S] [--new-only]  block until Claude Science writes to science/inbox/ (exit 0) or time out (exit 2)
  consult [status|on|off|sent IDS|hook] [--wait-minutes N] [--ask-gate on|off]
                                    auto-consult: `on`/`off` set policy.json "consult"; `status` lists
                                    open SQ items not yet sent to Claude Science, sent ones awaiting an
                                    answer, waiting findings and whether Claude Science is running;
                                    `sent SQ-004 ...` records items handed over; `hook` is the hook body,
                                    silent unless auto-consult is on and never run inside subagents:
                                    - SubagentStop, and PostToolUse on SubagentHandback: notes, without
                                      output, that a subagent handed back a GSD decision checkpoint or
                                      a blocked/inconclusive result;
                                    - PostToolUse (Agent/Task/Skill): adds a reminder to the model's
                                      context when there is consult work (deduped) or such a hand-back
                                      is noted;
                                    - PreToolUse (AskUserQuestion), the ask gate: refuses the call once,
                                      telling the model to consult Claude Science before it asks the
                                      owner to decide; the refusal carries any reminder still due. The
                                      same question asked again, a question that cites an SQ id, and
                                      any question within 30 minutes of a refusal pass. `--ask-gate
                                      off` (with `on`) disables it.
                                    Always exits 0.
  session-hint                      for the SessionStart hook: print up to 6 one-line reminders
                                    when there is something to act on, plus the standing auto-consult
                                    rule while auto-consult is on; otherwise print nothing.
                                    Always exits 0.

Every command except init and skill-zip locates the project by walking up from --project-dir
(default: $CLAUDE_PROJECT_DIR or the current directory) to the first directory that
contains science/policy.json.
"""

import argparse
import datetime as _dt
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = PLUGIN_ROOT / "templates"
CS_SKILL_DIR = PLUGIN_ROOT / "cs-skill" / "science-findings"
sys.path.insert(0, str(CS_SKILL_DIR / "scripts"))
from check_finding import validate_finding  # noqa: E402

SKILL_VERSION_RE = re.compile(r"^Skill version: (\d+\.\d+\.\d+)\s*$", re.MULTILINE)
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _plugin_version(plugin_dir: Path) -> Optional[str]:
    try:
        version = json.loads((plugin_dir / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return str(version) if version else None


def _skill_version(skill_md: Path) -> Optional[str]:
    try:
        m = SKILL_VERSION_RE.search(skill_md.read_text(encoding="utf-8"))
    except OSError:
        return None
    return m.group(1) if m else None


def vtuple(version: Any) -> Tuple[int, ...]:
    """'0.4.1' -> (0, 4, 1). Missing or malformed parts count as 0, so comparing never fails."""
    nums = []
    for part in str(version or "").strip().lstrip("vV").split(".")[:3]:
        m = re.match(r"\d+", part)
        nums.append(int(m.group(0)) if m else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


PLUGIN_VERSION = _plugin_version(PLUGIN_ROOT) or "0.0.0"
SKILL_VERSION = _skill_version(CS_SKILL_DIR / "SKILL.md") or "0.0.0"

GSD_SKILL_TEMPLATE = TEMPLATES / "gsd" / "science-context" / "SKILL.md"
GSD_SKILL_ENTRY = ".claude/skills/science-context"
GSD_DEFAULT_AGENTS = ("gsd-phase-researcher", "gsd-project-researcher", "gsd-advisor-researcher",
                      "gsd-assumptions-analyzer", "gsd-planner", "gsd-plan-checker", "gsd-roadmapper",
                      "gsd-executor", "gsd-debugger", "gsd-verifier")
QUESTIONS_REL = ".planning/research/questions.md"
REQUESTS_HEADING = "## For Claude Science"
REQUESTS_COMMENT = ("<!-- Request queue for Claude Science (science-bridge). One line per question:\n"
                    "     - [ ] SQ-NNN (YYYY-MM-DD, who/where): question — context: paths; affects: IDs\n"
                    "     Check for an existing item before adding. Claude Science answers with a finding that has\n"
                    "     `answers: [SQ-NNN]`; `bridge.py archive` ticks the item. -->")
REQUEST_LINE_RE = re.compile(r"^- \[( |x|X)\] (SQ-(\d{3,}))\b(.*)$")
MANIFEST_FORMAT = "science-bridge-manifest/v0.1"
PRIVACY_LEVELS = ("phi", "confidential", "public")
ACCESS_MODES = ("repo-readonly", "science-only")
ROUTES = ("note", "todo", "seed", "manual", "rejected")
OUTBOX_RESERVED = {"MANIFEST.json", ".gitkeep"}
EXPORT_REPO_READONLY = ("%s: Claude Science reads repo files directly in repo-readonly mode; export is only for "
                        "files outside the repo")
SELFTEST_NAME = "selftest.json"
SELFTEST_LATEST = "selftest-latest.json"
SELFTEST_FORMAT = "science-bridge-selftest/v1"
INBOX_IGNORED = {".gitkeep", "README.md", SELFTEST_NAME, ".selftest-probe"}

POLICY_VERSION = 3
EXPORT_DEFAULTS = (("allowed_sources", []), ("allowed_extensions", []), ("check_command", None), ("max_file_mb", 50))
EXPORT_KEYS = tuple(k for k, _ in EXPORT_DEFAULTS)
COMPUTE_REACH = ("claude-code", "claude-science")
HOST_RE = re.compile(r"^[A-Za-z0-9._-]+$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$")

MANAGED_START = "<!-- science-bridge:managed:start -->"
MANAGED_END = "<!-- science-bridge:managed:end -->"
BRIEF_HEADING = "## Project brief"
REQ_ID_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,7})-\d{2,3}\b")
REQ_DEF_RE = re.compile(r"\*\*([A-Z][A-Z0-9]{1,7})-\d{2,3}\*\*")
BRIEF_PLACEHOLDER = "<!-- Fill in: what the project is, what you want from Claude Science, key paths. -->"
SKILL_ZIP_SKIP = {"__pycache__", ".DS_Store"}
MAX_HINT_LINES = 6
SSH_TIMEOUT = 20

CONSULT_DEFAULT_WAIT = 120   # minutes a consult round waits for Claude Science before work continues
CONSULT_MAX_WAIT = 1440
CONSULT_REHINT_MINUTES = 30  # the hook repeats an unchanged reminder at most this often
CONSULT_GATE_SAME_HOURS = 24  # the ask gate lets the question it last refused through for this long
CONSULT_HANDBACK_MAX_MINUTES = 120  # a noted hand-back that nobody was reminded of is dropped after this
CONSULT_STATE_REL = "science/.cache/consult.json"  # gitignored (science/.gitignore has .cache/)
CONSULT_TAG = "[science-bridge auto-consult]"
SQ_ID_RE = re.compile(r"\bSQ-\d{3,}\b")
# What GSD agents return when they hand a decision or a blocker back to the orchestrator.
GSD_CHECKPOINT_RE = re.compile(r"^#{1,3} CHECKPOINT REACHED\b", re.MULTILINE)
GSD_CHECKPOINT_DECISION_RE = re.compile(r"^\*\*Type:\*\*\s*\[?\s*(?:checkpoint:)?decision\b", re.MULTILINE | re.IGNORECASE)
GSD_STUCK_RE = re.compile(r"^#{1,3} (?:[A-Z]+ )*(?:BLOCKED|INCONCLUSIVE)\b", re.MULTILINE)
CONSULT_ASK_HOW = ("queue it with `ask` as a neutral, self-contained question (every option, without your own "
                   "recommendation, with the context paths)")
CONSULT_RULE_TEXT = ("[science-bridge] auto-consult is on for this project. Consult Claude Science on your own "
                     "initiative, without waiting to be told: when you hit a hard problem that you can't settle with "
                     "your own tools, and before you ask the owner to decide a research, analysis or design question. "
                     "Load the bridge skill (/science-bridge:bridge), %s, run the consult round, then give the owner "
                     "its finding beside your own recommendation." % CONSULT_ASK_HOW)
CONSULT_GATE_TEXT = ("%s Auto-consult is on for this project, so Claude Science is consulted before the owner is "
                     "asked to decide. If this is a research, analysis or design decision and Claude Science hasn't "
                     "been consulted on it, don't ask yet: load the bridge skill (/science-bridge:bridge), %s, run "
                     "the consult round, then ask again, giving its finding beside your own recommendation and naming "
                     "the SQ id in the question. If it is not that kind of decision (a preference, a permission, "
                     "something Claude Science can't judge), or it has already been consulted, call AskUserQuestion "
                     "again with the same questions; they will go through."
                     % (CONSULT_TAG, CONSULT_ASK_HOW))
CONSULT_HANDBACK_TEXT = ("%s %%s returned %%s. Auto-consult is on for this project, so before you take "
                         "it to the owner or settle it on your own, decide whether Claude Science should weigh in. "
                         "If it is a research, analysis or design question, %s unless the agent already queued it, "
                         "and run the consult round (the \"consult\" section of the bridge skill, "
                         "/science-bridge:bridge consult). Then bring its finding to the owner beside your own "
                         "recommendation." % (CONSULT_TAG, CONSULT_ASK_HOW))
CS_CLI_ENV = "SCIENCE_BRIDGE_CS_CLI"  # tests: path to a fake `claude-science`, or "none"
CS_URL = "http://localhost:%d"

FIX_SELFTEST = 'In Claude Science, open a NEW session in this project and say "Run the science-bridge self-test".'
FIX_SKILL = ("Run /science-bridge:bridge cs-setup, upload the skill zip it builds in Claude Science (Settings › Skills › "
             "Edit & republish), then start a NEW Claude Science session and run the self-test again.")
FIX_REPO = ("In Claude Science, grant the repo root %s Read-only under Files › Artifacts › Add folder…, then start a "
            "NEW session and run the self-test again.")
FIX_READONLY = ("The repo-root grant must be Read-only, not Read & write: in Claude Science, Files › Artifacts, remove "
                "the repo-root folder and add it again as Read-only (keep science/ as Read & write).")
FIX_INBOX = "In Claude Science, grant %s Read & write under Files › Artifacts › Add folder…, then run the self-test again."


class BridgeError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> Optional[_dt.datetime]:
    if not isinstance(value, str):
        return None
    try:
        ts = _dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=_dt.timezone.utc)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _emit(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _json_pretty(obj: Any, level: int = 0) -> str:
    """JSON with 2-space indent that keeps short lists of scalars on one line (like the template)."""
    pad = "  " * level
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        items = ["%s  %s: %s" % (pad, json.dumps(k, ensure_ascii=False), _json_pretty(v, level + 1))
                 for k, v in obj.items()]
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"
    if isinstance(obj, list):
        inline = json.dumps(obj, ensure_ascii=False)
        if all(not isinstance(x, (dict, list)) for x in obj) and len(pad) + len(inline) <= 120:
            return inline
        return "[\n" + ",\n".join(pad + "  " + _json_pretty(x, level + 1) for x in obj) + "\n" + pad + "]"
    return json.dumps(obj, ensure_ascii=False)


def _write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _render(template: str, **values: str) -> str:
    """Replace {{name}} placeholders in one pass (inserted values are never re-expanded)."""
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m.group(1), m.group(0)), template)


def _out_path(arg: Optional[str]) -> Optional[Path]:
    if not arg:
        return None
    p = Path(arg).expanduser()
    return (p if p.is_absolute() else Path(os.getcwd()) / p).resolve()


def _start_dir(arg: Optional[str]) -> Path:
    return Path(arg or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).resolve()


def find_project(start: Path, max_up: int = 12) -> Optional[Path]:
    cur = start
    for _ in range(max_up):
        if (cur / "science" / "policy.json").is_file():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def require_project(arg: Optional[str]) -> Path:
    root = find_project(_start_dir(arg))
    if root is None:
        raise BridgeError("no science/policy.json found here or in any parent directory; run `init` first")
    return root


# ---------------------------------------------------------------- policy

def _read_policy_raw(path: Path) -> Dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BridgeError("cannot read %s: %s" % (path, e))
    if not isinstance(raw, dict):
        raise BridgeError("policy.json must be a JSON object")
    return raw


def normalize_policy(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a policy and return it in v3 shape. v1/v2 policies (export keys at the top level;
    v1 without `access`) are moved into `export` in memory and marked `_legacy: True`."""
    policy = dict(raw)
    version = policy.get("policy_version")
    if version is None:
        version_num = 1
    elif isinstance(version, int) and not isinstance(version, bool):
        version_num = version
    else:
        raise BridgeError("policy.json: policy_version must be an integer")
    if version_num > POLICY_VERSION:
        raise BridgeError("policy.json: policy_version %d is newer than this science-bridge (v%s) understands; "
                          "update the plugin" % (version_num, PLUGIN_VERSION))
    legacy = version_num < POLICY_VERSION
    export = policy.get("export")
    if export is not None and not isinstance(export, dict):
        raise BridgeError("policy.json: export must be an object")
    export = dict(export or {})
    if legacy:
        for key in EXPORT_KEYS:
            if key in policy:
                export[key] = policy.pop(key)
    else:
        stray = [k for k in EXPORT_KEYS if k in policy]
        if stray:
            raise BridgeError("policy.json: %s belong(s) inside the \"export\" block in policy v3"
                              % ", ".join(stray))
    for key, default in EXPORT_DEFAULTS:
        if key not in export:
            export[key] = list(default) if isinstance(default, list) else default
    for key in ("allowed_sources", "allowed_extensions"):
        if not isinstance(export[key], list):
            raise BridgeError("policy.json: export.%s must be a list" % key)
    if export["check_command"] is not None and not isinstance(export["check_command"], str):
        raise BridgeError("policy.json: export.check_command must be a string or null")
    try:
        float(export["max_file_mb"])
    except (TypeError, ValueError):
        raise BridgeError("policy.json: export.max_file_mb must be a number")
    policy["export"] = export

    if policy.get("privacy") not in PRIVACY_LEVELS:
        raise BridgeError("policy.json: privacy must be one of %s" % ", ".join(PRIVACY_LEVELS))
    policy.setdefault("access", "science-only")  # policies written before v0.3 had no access key
    if policy["access"] not in ACCESS_MODES:
        raise BridgeError("policy.json: access must be one of %s" % ", ".join(ACCESS_MODES))
    policy.setdefault("do_not_read", [])
    if not isinstance(policy["do_not_read"], list):
        raise BridgeError("policy.json: do_not_read must be a list of glob patterns")
    compute = policy.setdefault("compute", {})
    if not isinstance(compute, dict):
        raise BridgeError("policy.json: compute must be an object mapping host names to {\"reach\", \"notes\"}")
    for host, spec in compute.items():
        if not HOST_RE.match(host) or host.startswith("-"):
            raise BridgeError("policy.json: compute host %r must match %s and not start with '-'"
                              % (host, HOST_RE.pattern))
        if not isinstance(spec, dict) or spec.get("reach") not in COMPUTE_REACH:
            raise BridgeError("policy.json: compute.%s.reach must be one of %s" % (host, ", ".join(COMPUTE_REACH)))
        if "notes" in spec and not isinstance(spec["notes"], str):
            raise BridgeError("policy.json: compute.%s.notes must be a string" % host)
    policy.setdefault("skill_min_version", "0.0.0")
    if not isinstance(policy["skill_min_version"], str) or not VERSION_RE.match(policy["skill_min_version"]):
        raise BridgeError("policy.json: skill_min_version must look like 0.4.0")
    consult = policy.get("consult")
    if consult is not None:
        if not isinstance(consult, dict):
            raise BridgeError("policy.json: consult must be an object like {\"auto\": false, \"wait_minutes\": %d}"
                              % CONSULT_DEFAULT_WAIT)
        if "auto" in consult and not isinstance(consult["auto"], bool):
            raise BridgeError("policy.json: consult.auto must be true or false")
        if "ask_gate" in consult and not isinstance(consult["ask_gate"], bool):
            raise BridgeError("policy.json: consult.ask_gate must be true or false")
        wait = consult.get("wait_minutes", CONSULT_DEFAULT_WAIT)
        if isinstance(wait, bool) or not isinstance(wait, (int, float)) or not 0 <= wait <= CONSULT_MAX_WAIT:
            raise BridgeError("policy.json: consult.wait_minutes must be a number from 0 to %d" % CONSULT_MAX_WAIT)
    policy["_legacy"] = legacy
    return policy


def load_policy(root: Path, for_export: bool = False) -> Dict[str, Any]:
    policy = normalize_policy(_read_policy_raw(root / "science" / "policy.json"))
    if for_export and policy["privacy"] == "phi" and not policy["export"]["allowed_sources"]:
        raise BridgeError("policy.json: privacy 'phi' requires a non-empty export.allowed_sources whitelist")
    return policy


def policy_as_v3(raw: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Any]:
    """The on-disk v3 form of a (normalised) legacy policy. Unknown keys are kept, after the known ones."""
    out = {"policy_version": POLICY_VERSION}  # type: Dict[str, Any]
    for key in ("access", "do_not_read", "privacy", "skill_min_version", "compute", "export"):
        out[key] = policy[key]
    for key, value in raw.items():
        if key not in out and key not in EXPORT_KEYS:
            out[key] = value
    return out


def load_manifest(root: Path) -> Dict[str, Any]:
    path = root / "science" / "outbox" / "MANIFEST.json"
    if not path.exists():
        return {"format": MANIFEST_FORMAT, "files": []}
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise BridgeError("outbox/MANIFEST.json is not valid JSON: %s" % e)
    if manifest.get("format") != MANIFEST_FORMAT:
        raise BridgeError("outbox/MANIFEST.json has unknown format %r" % manifest.get("format"))
    return manifest


def save_manifest(root: Path, manifest: Dict[str, Any]) -> None:
    path = root / "science" / "outbox" / "MANIFEST.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(path))


# ---------------------------------------------------------------- init

def init_policy(root: Path) -> Tuple[str, Optional[str], bool]:
    """Create or upgrade science/policy.json. Returns (status, skill_min_version note, ok)."""
    path = root / "science" / "policy.json"
    status = None  # type: Optional[str]
    if not path.exists():
        shutil.copyfile(str(TEMPLATES / "policy.json"), str(path))
        status = "created"
    try:
        raw = _read_policy_raw(path)
        policy = normalize_policy(raw)
    except BridgeError as e:
        return "invalid, left unchanged: %s" % e, None, False
    new = raw
    if policy["_legacy"]:
        new = policy_as_v3(raw, policy)
        status = "migrated v%s→v%d" % (raw.get("policy_version") or 1, POLICY_VERSION)
    old_min = policy["skill_min_version"]
    note = old_min
    if vtuple(SKILL_VERSION) > vtuple(old_min):
        if new is raw:
            new = dict(raw)
        new["skill_min_version"] = SKILL_VERSION
        note = "%s (raised from %s)" % (SKILL_VERSION, old_min)
        status = status or "updated"
    if new is not raw:
        normalize_policy(new)
        _write_text_atomic(path, _json_pretty(new) + "\n")
    return status or "unchanged", note, True


def _split_managed(text: str) -> Optional[Tuple[str, str, str]]:
    start = text.find(MANAGED_START)
    if start < 0:
        return None
    end = text.find(MANAGED_END, start)
    if end < 0:
        return None
    end += len(MANAGED_END)
    return text[:start], text[start:end], text[end:]


def _readme_title_name(text: str) -> Optional[str]:
    for line in text.splitlines():
        if line.startswith("# "):
            m = re.match(r"^# Science handoff:\s*(.+?)\s*$", line)
            name = m.group(1) if m else None
            if name and "{{" not in name and not (name.startswith("<") and name.endswith(">")):
                return name
            return None
    return None


def _project_name(root: Path) -> str:
    text = _read_text(root / ".planning" / "PROJECT.md")
    if text:
        for line in text.splitlines():
            if line.startswith("# ") and line[2:].strip():
                return line[2:].strip()
    text = _read_text(root / "science" / "README.md")
    name = _readme_title_name(text) if text else None
    return name or root.name


def _first_paragraph(text: str, heading: str) -> Optional[str]:
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip().lower() == heading.lower())
    except StopIteration:
        return None
    para = []  # type: List[str]
    in_comment = False
    for line in lines[start + 1:]:
        s = line.strip()
        if in_comment:
            in_comment = "-->" not in s
            continue
        if not para and s.startswith("<!--"):
            in_comment = "-->" not in s
            continue
        if s.startswith("#"):
            break
        if not s:
            if para:
                break
            continue
        para.append(s)
    return " ".join(para) or None


def _requirement_prefixes(root: Path) -> Optional[str]:
    text = _read_text(root / ".planning" / "REQUIREMENTS.md")
    if not text:
        return None
    seen = set()
    counts = {}  # type: Dict[str, int]
    # GSD defines requirements as "**ID**:" (usually in a checklist); count those. Fall back to any
    # ID-shaped token only when no definitions are found, so cross-references like [TS-34] don't count.
    defined = list(REQ_DEF_RE.finditer(text))
    for m in (defined or REQ_ID_RE.finditer(text)):
        if m.group(0) in seen:
            continue
        seen.add(m.group(0))
        counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return ", ".join("%s (%d)" % (prefix, n) for prefix, n in counts.items()) or None


def auto_brief(root: Path) -> str:
    lines = []
    text = _read_text(root / ".planning" / "PROJECT.md")
    what = _first_paragraph(text, "## What This Is") if text else None
    core = _first_paragraph(text, "## Core Value") if text else None
    if what:
        lines.append("**What the project is:** " + what)
    if core:
        lines.append("**Core value:** " + core)
    if not what and not core:
        lines.append(BRIEF_PLACEHOLDER)
    prefixes = _requirement_prefixes(root)
    if prefixes:
        lines.append("**Requirement ID prefixes for `affects`:** " + prefixes)
    lines.append("**Notes for Claude Science:** <!-- standing tasks, data caveats, anything else -->")
    return "\n\n".join(lines)


def _legacy_brief(text: str) -> str:
    lines = text.splitlines()
    idx = next((i for i, line in enumerate(lines) if line.strip() == BRIEF_HEADING), None)
    if idx is not None:
        body = lines[idx + 1:]
    else:
        title = next((i for i, line in enumerate(lines) if line.startswith("# ")), None)
        body = lines if title is None else lines[:title] + lines[title + 1:]
    return "\n".join(body).strip("\n")


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while path.with_name("%s%d" % (path.name, n)).exists():
        n += 1
    return path.with_name("%s%d" % (path.name, n))


def sync_readme(root: Path) -> Tuple[str, Optional[str]]:
    """Create science/README.md, refresh its managed block, or migrate a legacy README.
    Returns (status, backup path of a migrated legacy README or None)."""
    template = (TEMPLATES / "README.md").read_text(encoding="utf-8")
    tpl_parts = _split_managed(template)
    dst = root / "science" / "README.md"
    if not dst.exists():
        dst.write_text(_render(template, project_name=_project_name(root), brief=auto_brief(root)), encoding="utf-8")
        return "created", None
    old = dst.read_text(encoding="utf-8")
    parts = _split_managed(old)
    if parts is not None:
        if tpl_parts is None:
            return "unchanged", None
        new = parts[0] + _render(tpl_parts[1], project_name=_project_name(root)) + parts[2]
        if new == old:
            return "unchanged", None
        _write_text_atomic(dst, new)
        return "managed-block-updated", None
    name = _readme_title_name(old) or _project_name(root)
    brief = _legacy_brief(old) or auto_brief(root)
    backup = _unique_path(dst.with_name("README.md.bak"))
    shutil.copyfile(str(dst), str(backup))
    _write_text_atomic(dst, _render(template, project_name=name, brief=brief))
    return "migrated-legacy", str(backup.relative_to(root))


def cmd_init(args: argparse.Namespace) -> int:
    root = _start_dir(args.project_dir)
    sci = root / "science"
    created = []  # type: List[str]
    for sub in ("outbox", "inbox", "archive", "runs"):
        d = sci / sub
        if not d.exists():
            d.mkdir(parents=True)
            created.append(str(d.relative_to(root)) + "/")
        keep = d / ".gitkeep"
        if not keep.exists() and sub != "outbox":
            keep.write_text("")
    ignore = sci / ".gitignore"  # cs-setup builds the skill zip in science/.cache/ (inside the project, so a
    if not ignore.exists():      # browser automation tool may upload it); keep it out of git
        ignore.write_text(".cache/\n")
        created.append(str(ignore.relative_to(root)))
    policy_status, min_version, policy_ok = init_policy(root)
    if policy_status == "created":
        created.append("science/policy.json")
    readme_status, readme_backup = sync_readme(root)
    if readme_status == "created":
        created.append("science/README.md")
    manifest = sci / "outbox" / "MANIFEST.json"
    if not manifest.exists():
        save_manifest(root, {"format": MANIFEST_FORMAT, "files": []})
        created.append(str(manifest.relative_to(root)))
    gsd = None
    if not args.no_gsd and (root / ".planning" / "config.json").is_file():
        gsd = gsd_setup(root, list(GSD_DEFAULT_AGENTS))
    next_steps = [
        "Review science/policy.json: access, do_not_read, privacy, compute (the export block only "
        "controls bridge.py export).",
        "Review the Project brief at the end of science/README.md.",
        "Run /science-bridge:bridge cs-setup for the one-time Claude Science setup (skill upload, folder "
        "grants, instructions), then the Claude Science self-test and /science-bridge:bridge verify.",
    ]
    if readme_backup:
        next_steps.insert(2, "Your old science/README.md is kept at %s; move anything still needed into the "
                             "Project brief, then delete it." % readme_backup)
    redundant = []  # type: List[Dict[str, str]]
    if policy_ok:
        try:
            access = load_policy(root)["access"]
            redundant = _outbox_report(root, access)["redundant"] if access == "repo-readonly" else []
        except (BridgeError, OSError):
            redundant = []
    if redundant:
        next_steps.append("science/outbox has %d old copies of repo files; Claude Science now reads the repo "
                          "directly. Run /science-bridge:bridge outbox-clean to remove them." % len(redundant))
    out = {
        "ok": policy_ok,
        "project": str(root),
        "created": created,
        "policy": policy_status,
        "skill_min_version": min_version,
        "readme": readme_status,
    }  # type: Dict[str, Any]
    if readme_backup:
        out["readme_backup"] = readme_backup
    out["gsd"] = gsd if gsd is not None else "skipped (no .planning/config.json or --no-gsd)"
    out["outbox_redundant"] = redundant
    out["next_steps"] = next_steps
    _emit(out)
    return 0 if policy_ok else 1


# ---------------------------------------------------------------- Claude Science setup

def build_skill_zip(out: Optional[Path] = None) -> Dict[str, Any]:
    src = CS_SKILL_DIR
    if not (src / "SKILL.md").is_file():
        raise BridgeError("skill source %s/SKILL.md not found" % src)
    version = _skill_version(src / "SKILL.md") or SKILL_VERSION
    if out is None:
        out = Path(tempfile.gettempdir()) / "science-bridge" / ("science-findings-%s.zip" % version)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    files = []  # type: List[str]
    with zipfile.ZipFile(str(tmp), "w", compression=zipfile.ZIP_DEFLATED, strict_timestamps=False) as z:
        for dirpath, dirnames, filenames in os.walk(str(src)):
            dirnames[:] = sorted(d for d in dirnames if d not in SKILL_ZIP_SKIP)
            arc_dir = (Path("science-findings") / Path(dirpath).relative_to(src)).as_posix()
            z.write(dirpath, arc_dir)
            for name in sorted(filenames):
                if name in SKILL_ZIP_SKIP or name.endswith(".pyc"):
                    continue
                z.write(os.path.join(dirpath, name), arc_dir + "/" + name)
                files.append(arc_dir + "/" + name)
    os.replace(str(tmp), str(out))
    return {"zip": str(out), "skill_version": version, "files": files}


def cmd_skill_zip(args: argparse.Namespace) -> int:
    _emit({"ok": True, **build_skill_zip(_out_path(args.out))})
    return 0


def cmd_cs_setup(args: argparse.Namespace) -> int:
    root = require_project(args.project_dir)
    policy = load_policy(root)
    name = _project_name(root)
    sci = root / "science"
    if policy["access"] == "repo-readonly":
        grants = [{"path": str(root), "mode": "Read-only"}, {"path": str(sci), "mode": "Read & write"}]
    else:
        grants = [{"path": str(sci), "mode": "Read & write"}]
    instructions = _render((TEMPLATES / "cs-instructions.txt").read_text(encoding="utf-8"),
                           project_name=name, root=str(root), skill_min_version=policy["skill_min_version"])
    if policy["access"] == "science-only":
        instructions = re.sub(r"(?m)^Read access:.*$",
                              "Read access (access: science-only): only the files listed in "
                              "science/outbox/MANIFEST.json, never the paths in policy.json \"do_not_read\".",
                              instructions)
    # Default: inside the project (science/.cache/, git-ignored), so Claude in Chrome can upload it from a
    # session started in this project; system temp dirs are usually outside what the browser tool may read.
    out = _out_path(args.out) or (root / "science" / ".cache" / ("science-findings-%s.zip" % SKILL_VERSION))
    skill = build_skill_zip(out)
    suggested = "%s (science)" % name
    steps = [
        "Claude Science › Settings › Skills: if science-findings is already listed, open it and choose Edit & "
        "republish; otherwise choose Add skill › Upload a skill. Upload %s and publish it (skill v%s)."
        % (skill["zip"], skill["skill_version"]),
        "Create a Claude Science project named \"%s\" (or open the one you already use for this repo)." % suggested,
    ]
    steps += ["In that project, open Files › Artifacts › Add folder… and add %s as %s." % (g["path"], g["mode"])
              for g in grants]
    steps += [
        "Open Project settings › Instructions for Claude and paste the `instructions` text (replace any older "
        "science-bridge instructions).",
        "Open a NEW session in the project (a session loads skills and instructions when it starts) and say "
        "\"Run the science-bridge self-test\".",
        "Back in Claude Code, run /science-bridge:bridge verify; it checks the self-test result and says what to fix.",
        "While Claude Science works it may stop at \"Waiting for your approval\" (package installs, network domains, "
        "compute). If a task shows no progress for ~2 minutes, look; approve prompts that fit the task with scope "
        "\"This project\".",
    ]
    warnings = []
    if policy["_legacy"]:
        warnings.append("science/policy.json is a legacy policy; run /science-bridge:bridge init first.")
    if vtuple(policy["skill_min_version"]) < vtuple(skill["skill_version"]):
        warnings.append("policy.json skill_min_version %s is below the bundled skill v%s; run /science-bridge:bridge "
                        "init to raise it." % (policy["skill_min_version"], skill["skill_version"]))
    _emit({
        "ok": True,
        "project": str(root),
        "project_name": name,
        "suggested_cs_project_name": suggested,
        "access": policy["access"],
        "grants": grants,
        "instructions": instructions,
        "skill_zip": skill["zip"],
        "skill_version": skill["skill_version"],
        "skill_min_version": policy["skill_min_version"],
        "steps": steps,
        "warnings": warnings,
    })
    return 0


# ---------------------------------------------------------------- verify / compute

def _check(name: str, ok: bool, detail: str, fix: Optional[str] = None) -> Dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail, "fix": None if ok else fix}


def _obj(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def ssh_check(host: str) -> Tuple[bool, str]:
    """Can Claude Code reach HOST without a prompt? $SCIENCE_BRIDGE_SSH replaces the ssh binary (tests)."""
    ssh = os.environ.get("SCIENCE_BRIDGE_SSH") or "ssh"
    argv = [ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, "true"]
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=SSH_TIMEOUT)
    except FileNotFoundError:
        return False, "%s not found" % ssh
    except subprocess.TimeoutExpired:
        return False, "no answer within %d s" % SSH_TIMEOUT
    except OSError as e:
        return False, str(e)
    if proc.returncode == 0:
        return True, "`ssh %s true` succeeded" % host
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:]
    return False, "`ssh %s true` exited %d%s" % (host, proc.returncode, (": " + tail[0]) if tail else "")


def compute_checks(policy: Dict[str, Any], selftest: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """claude-code hosts: ssh from here. claude-science hosts (only with a self-test): its reported result."""
    reported = {}  # type: Dict[str, Dict[str, Any]]
    if selftest is not None:
        entries = selftest.get("compute")
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and entry.get("name"):
                reported[str(entry["name"])] = entry
    checks = []
    for host in sorted(policy["compute"]):
        spec = policy["compute"][host]
        notes = spec.get("notes")
        if spec["reach"] == "claude-code":
            ok, detail = ssh_check(host)
            checks.append(_check("compute:%s" % host, ok, "from Claude Code: %s" % detail,
                                 "Run `! ssh %s` once in Claude Code to log in (Claude Code reuses that login), "
                                 "then run /science-bridge:bridge compute-check." % host))
        elif selftest is not None:
            entry = reported.get(host)
            if entry is None:
                checks.append(_check("compute:%s" % host, False, "the self-test did not report on %s" % host,
                                     "Hosts with reach \"claude-science\" must be tested by Claude Science: "
                                     + FIX_SELFTEST))
                continue
            result = entry.get("result")
            detail = "Claude Science reports %s%s" % (result, (": %s" % entry["detail"]) if entry.get("detail") else "")
            checks.append(_check("compute:%s" % host, result == "ok", detail,
                                 "Claude Science could not reach %s%s. Fix its access from Claude Science, then run "
                                 "the self-test again." % (host, (" (%s)" % notes) if notes else "")))
    return checks


def _git_local(root: Path, *args: str) -> Optional[bytes]:
    try:
        proc = subprocess.run(["git", "-C", str(root)] + list(args), stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _git_head(root: Path) -> Optional[str]:
    out = _git_local(root, "rev-parse", "HEAD")
    return out.decode("utf-8", "replace").strip() if out else None


def _git_tracked_count(root: Path) -> Optional[int]:
    out = _git_local(root, "ls-files", "-z")
    return None if out is None else len([t for t in out.split(b"\0") if t])


def _git_is_ancestor(root: Path, older: str, newer: str) -> bool:
    try:
        proc = subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", older, newer],
                              stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _repo_checks(root: Path, policy: Dict[str, Any], data: Dict[str, Any]) -> List[Dict[str, Any]]:
    checks = []
    fix_repo = FIX_REPO % root
    repo = _obj(data.get("repo"))
    error = repo.get("error")
    probe = _obj(repo.get("probe"))
    local_head = _git_head(root)
    if local_head is None and error and error == repo.get("git_error"):
        error = None  # no git repository on either side; the file probe still has to match
    read_ok = not error and bool(probe.get("path"))
    checks.append(_check("repo_read", read_ok,
                         "Claude Science read the repo (git: %s)" % (repo.get("git") or "none") if read_ok
                         else "Claude Science could not read the repo: %s" % (error or "no probe file"), fix_repo))

    cs_head = repo.get("git_head")
    cs_head = cs_head.strip() if isinstance(cs_head, str) else None
    heads_equal = False
    if local_head is None:
        checks.append(_check("repo_head", cs_head is None,
                             "this project is not a git repository here" + (
                                 ", but Claude Science reported HEAD %s" % cs_head if cs_head else ""), fix_repo))
    elif not cs_head:
        checks.append(_check("repo_head", False, "Claude Science reported no git HEAD (this repo: %s)"
                             % local_head[:12], fix_repo))
    elif cs_head == local_head:
        heads_equal = True
        checks.append(_check("repo_head", True, "same HEAD %s" % local_head[:12]))
    elif COMMIT_RE.match(cs_head) and _git_is_ancestor(root, cs_head, local_head):
        checks.append(_check("repo_head", True, "Claude Science saw %s, an ancestor of HEAD %s (commits made after "
                                                "the self-test)" % (cs_head[:12], local_head[:12])))
    else:
        checks.append(_check("repo_head", False, "Claude Science saw commit %s, which is not in this repo's history "
                                                 "(HEAD %s); it is reading a different copy of the repo"
                             % (cs_head[:12], local_head[:12]), fix_repo))
    if heads_equal:
        local_n = _git_tracked_count(root)
        cs_n = repo.get("tracked_files")
        checks.append(_check("repo_files", cs_n == local_n,
                             "tracked files: Claude Science %s, here %s" % (cs_n, local_n),
                             "If you staged or removed files since the self-test, run it again; otherwise " + fix_repo))

    rel = probe.get("path")
    if isinstance(rel, str) and rel:
        local = None  # type: Optional[Path]
        try:
            candidate = (root / rel).resolve()
            candidate.relative_to(root.resolve())
            local = candidate if candidate.is_file() else None
        except (OSError, ValueError):
            local = None
        if local is None:
            checks.append(_check("repo_probe", False, "probe file %s does not exist here" % rel, fix_repo))
        else:
            same = _sha256(local) == probe.get("sha256")
            checks.append(_check("repo_probe", same, "sha256 of %s %s" % (
                rel, "matches" if same else "differs (edited since the self-test, or a different copy of the repo)"),
                "If you edited %s since the self-test, run it again; otherwise %s" % (rel, fix_repo)))

    cs_open = data.get("requests_open")
    local_open = sorted(r["id"] for r in list_requests(root) if r["open"])
    if not isinstance(cs_open, list):
        checks.append(_check("requests_open", False, "the self-test did not report open requests", FIX_SELFTEST))
    else:
        cs_set, local_set = set(str(x) for x in cs_open), set(local_open)
        if cs_set == local_set:
            detail = "open SQ items match: %s" % (", ".join(local_open) or "none")
        else:
            detail = "open SQ items differ; only in Claude Science's view: %s; only here: %s" % (
                ", ".join(sorted(cs_set - local_set)) or "none", ", ".join(sorted(local_set - cs_set)) or "none")
        if data.get("requests_error"):
            detail += " (self-test: %s)" % data["requests_error"]
        checks.append(_check("requests_open", cs_set == local_set, detail,
                             "If %s changed after the self-test, run it again; otherwise %s" % (QUESTIONS_REL, fix_repo)))
    return checks


def selftest_checks(root: Path, policy: Dict[str, Any], data: Dict[str, Any], max_age_hours: float) -> List[Dict[str, Any]]:
    checks = []
    fmt = data.get("format")
    checks.append(_check("format", fmt == SELFTEST_FORMAT, "format %r" % (fmt,),
                         "selftest.json was not written by the skill's scripts/selftest.py. " + FIX_SELFTEST))
    created = _parse_utc(data.get("created"))
    if created is None:
        checks.append(_check("fresh", False, "created is missing or not an ISO time: %r" % (data.get("created"),),
                             FIX_SELFTEST))
    else:
        age = (_dt.datetime.now(_dt.timezone.utc) - created).total_seconds() / 3600.0
        checks.append(_check("fresh", age <= max_age_hours, "created %s (%.1f h ago; limit %g h)"
                             % (data.get("created"), age, max_age_hours), FIX_SELFTEST))

    cs_version = data.get("skill_version")
    need = policy["skill_min_version"]
    version_ok = isinstance(cs_version, str) and bool(VERSION_RE.match(cs_version)) and vtuple(cs_version) >= vtuple(need)
    checks.append(_check("skill_version", version_ok,
                         "Claude Science runs science-findings %s; policy.json requires >= %s (bundled: %s)"
                         % (cs_version or "unknown", need, SKILL_VERSION), FIX_SKILL))

    sci = root / "science"
    cs_policy = _obj(data.get("policy"))
    if cs_policy.get("ok") is not True:
        checks.append(_check("policy", False, "Claude Science could not read science/policy.json: %s"
                             % (cs_policy.get("error") or "unknown error"), FIX_INBOX % sci))
    else:
        same = (cs_policy.get("access") == policy["access"]
                and cs_policy.get("do_not_read_count") == len(policy["do_not_read"]))
        checks.append(_check("policy", same, "Claude Science saw access %s with %s do_not_read pattern(s); here: %s "
                             "with %d" % (cs_policy.get("access"), cs_policy.get("do_not_read_count"),
                                          policy["access"], len(policy["do_not_read"])),
                             "Claude Science read a different policy.json. Check that its folder grants point at %s, "
                             "then start a NEW session and run the self-test again." % root))

    if policy["access"] == "repo-readonly":
        checks.extend(_repo_checks(root, policy, data))

    probe = data.get("write_probe_repo_root")
    if policy["access"] == "repo-readonly":
        probe_ok = probe == "denied"
    else:
        probe_ok = probe != "allowed"
    probe_fix = FIX_READONLY if probe == "allowed" else FIX_REPO % root
    checks.append(_check("write_probe_repo_root", probe_ok,
                         "creating %s/.science-bridge-write-probe from Claude Science: %s" % (root, probe), probe_fix))
    inbox = data.get("inbox_write")
    checks.append(_check("inbox_write", inbox == "ok", "writing science/inbox/ from Claude Science: %s" % inbox,
                         FIX_INBOX % sci))
    checks.extend(compute_checks(policy, data))
    return checks


def cmd_verify(args: argparse.Namespace) -> int:
    root = require_project(args.project_dir)
    policy = load_policy(root)
    path = root / "science" / "inbox" / SELFTEST_NAME
    checks = []
    data = None  # type: Optional[Dict[str, Any]]
    if not path.is_file():
        checks.append(_check("present", False, "science/inbox/%s not found" % SELFTEST_NAME, FIX_SELFTEST))
    else:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("not a JSON object")
            data = loaded
            checks.append(_check("present", True, "science/inbox/%s" % SELFTEST_NAME))
        except (OSError, ValueError) as e:
            checks.append(_check("present", False, "science/inbox/%s is not valid JSON: %s" % (SELFTEST_NAME, e),
                                 FIX_SELFTEST))
    if data is not None:
        checks.extend(selftest_checks(root, policy, data, args.max_age_hours))
    ok = all(c["ok"] for c in checks)
    out = {"ok": ok, "checks": checks}  # type: Dict[str, Any]
    if ok:
        archive = root / "science" / "archive"
        archive.mkdir(exist_ok=True)
        os.replace(str(path), str(archive / SELFTEST_LATEST))
        out["archived"] = "science/archive/%s" % SELFTEST_LATEST
    else:
        out["failed"] = [c["name"] for c in checks if not c["ok"]]
    _emit(out)
    return 0 if ok else 1


def cmd_compute_check(args: argparse.Namespace) -> int:
    root = require_project(args.project_dir)
    policy = load_policy(root)
    checks = compute_checks(policy, None)
    ok = all(c["ok"] for c in checks)
    out = {"ok": ok, "checks": checks}  # type: Dict[str, Any]
    if not checks:
        out["note"] = "policy.json compute has no host with reach \"claude-code\""
    _emit(out)
    return 0 if ok else 1


def _last_selftest(root: Path) -> Optional[Dict[str, Any]]:
    text = _read_text(root / "science" / "archive" / SELFTEST_LATEST)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    data = _obj(data)
    return {"created": data.get("created"), "skill_version": data.get("skill_version")}


# ---------------------------------------------------------------- GSD integration

def _gsd_skill_state(root: Path) -> str:
    dst = root / GSD_SKILL_ENTRY / "SKILL.md"
    if not dst.is_file():
        return "missing"
    return "current" if dst.read_bytes() == GSD_SKILL_TEMPLATE.read_bytes() else "outdated"


def gsd_report(root: Path) -> Dict[str, Any]:
    cfg_path = root / ".planning" / "config.json"
    if not cfg_path.is_file():
        return {"planning": False}
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except ValueError:
        return {"planning": True, "error": ".planning/config.json is not valid JSON"}
    skills = cfg.get("agent_skills") or {}
    configured = []
    for agent, val in skills.items():
        vals = [val] if isinstance(val, str) else (val if isinstance(val, list) else [])
        if GSD_SKILL_ENTRY in vals:
            configured.append(agent)
    return {"planning": True, "skill": _gsd_skill_state(root), "agents_configured": sorted(configured),
            "agents_missing": [a for a in GSD_DEFAULT_AGENTS if a not in configured]}


def gsd_setup(root: Path, agents: List[str]) -> Dict[str, Any]:
    cfg_path = root / ".planning" / "config.json"
    if not cfg_path.is_file():
        raise BridgeError("no .planning/config.json; gsd-setup needs a GSD project (run /gsd-new-project first)")
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise BridgeError(".planning/config.json is not valid JSON: %s" % e)
    for a in agents:
        if not a or not all(c.isalnum() or c in "-_" for c in a):
            raise BridgeError("invalid agent type %r" % a)

    dst = root / GSD_SKILL_ENTRY / "SKILL.md"
    state = _gsd_skill_state(root)
    if state != "current":
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(GSD_SKILL_TEMPLATE), str(dst))
    skill_status = {"missing": "installed", "outdated": "updated", "current": "unchanged"}[state]

    skills = cfg.get("agent_skills")
    if not isinstance(skills, dict):
        skills = {}
    added = []
    for agent in agents:
        val = skills.get(agent)
        vals = [val] if isinstance(val, str) else (list(val) if isinstance(val, list) else [])
        if GSD_SKILL_ENTRY not in vals:
            vals.append(GSD_SKILL_ENTRY)
            added.append(agent)
        skills[agent] = vals
    cfg["agent_skills"] = skills
    if added:
        tmp = cfg_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(str(tmp), str(cfg_path))

    verified = None  # type: Optional[bool]
    sdk = shutil.which("gsd-sdk")
    if sdk and agents:
        try:
            proc = subprocess.run([sdk, "query", "agent-skills", agents[0]], cwd=str(root),
                                  capture_output=True, text=True, timeout=60)
            verified = GSD_SKILL_ENTRY + "/SKILL.md" in proc.stdout
        except (OSError, subprocess.TimeoutExpired):
            verified = None
    section_added = ensure_requests_section(root)
    return {"skill": "%s/SKILL.md (%s)" % (GSD_SKILL_ENTRY, skill_status), "agents_added": added,
            "requests_section": "%s (%s)" % (QUESTIONS_REL, "added" if section_added else "present"),
            "agents": agents, "gsd_sdk_verified": verified}


def _questions_path(root: Path) -> Path:
    return root / QUESTIONS_REL


def ensure_requests_section(root: Path) -> bool:
    """Create .planning/research/questions.md and/or its "For Claude Science" section. True if changed."""
    if not (root / ".planning").is_dir():
        return False
    path = _questions_path(root)
    text = path.read_text(encoding="utf-8") if path.is_file() else "# Open Research Questions\n"
    if any(line.strip() == REQUESTS_HEADING for line in text.splitlines()):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip("\n") + "\n\n" + REQUESTS_HEADING + "\n\n" + REQUESTS_COMMENT + "\n", encoding="utf-8")
    return True


def _section_bounds(lines: List[str]) -> Optional[range]:
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == REQUESTS_HEADING)
    except StopIteration:
        return None
    end = next((j for j in range(start + 1, len(lines)) if lines[j].startswith("## ")), len(lines))
    return range(start + 1, end)


def list_requests(root: Path) -> List[Dict[str, Any]]:
    path = _questions_path(root)
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    bounds = _section_bounds(lines)
    out = []
    for i in (bounds or []):
        m = REQUEST_LINE_RE.match(lines[i].strip())
        if m:
            out.append({"id": m.group(2), "open": m.group(1) == " ", "text": m.group(4).strip()})
    return out


def tick_requests(root: Path, ids: List[str], answered_by: str) -> Dict[str, List[str]]:
    path = _questions_path(root)
    ticked, already, unknown = [], [], list(ids)
    if not path.is_file():
        return {"ticked": ticked, "already": already, "unknown": unknown}
    lines = path.read_text(encoding="utf-8").splitlines()
    for i in (_section_bounds(lines) or []):
        m = REQUEST_LINE_RE.match(lines[i].strip())
        if not m or m.group(2) not in ids:
            continue
        unknown = [u for u in unknown if u != m.group(2)]
        if m.group(1) == " ":
            lines[i] = "- [x] %s%s — answered: %s" % (m.group(2), m.group(4).rstrip(), answered_by)
            ticked.append(m.group(2))
        else:
            already.append(m.group(2))
    if ticked:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ticked": ticked, "already": already, "unknown": unknown}


def note_progress(root: Path, ids: List[str], ref: str) -> Dict[str, List[str]]:
    """For `partial: true` findings: append ' — progress: REF' to each open item (once per REF)."""
    path = _questions_path(root)
    progress, already, closed, unknown = [], [], [], list(ids)
    result = {"progress": progress, "already": already, "closed": closed, "unknown": unknown}
    if not path.is_file():
        return result
    note = " — progress: %s" % ref
    lines = path.read_text(encoding="utf-8").splitlines()
    for i in (_section_bounds(lines) or []):
        m = REQUEST_LINE_RE.match(lines[i].strip())
        if not m or m.group(2) not in ids:
            continue
        unknown[:] = [u for u in unknown if u != m.group(2)]
        if m.group(1) != " ":
            closed.append(m.group(2))
        elif note.strip() in lines[i]:
            already.append(m.group(2))
        else:
            lines[i] = lines[i].rstrip() + note
            progress.append(m.group(2))
    if progress:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def cmd_requests(args: argparse.Namespace) -> int:
    root = require_project(args.project_dir)
    items = list_requests(root)
    shown = items if args.all else [r for r in items if r["open"]]
    _emit({"file": QUESTIONS_REL, "open": sum(1 for r in items if r["open"]),
           "answered": sum(1 for r in items if not r["open"]), "items": shown})
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    root = require_project(args.project_dir)
    if not (root / ".planning").is_dir():
        raise BridgeError("no .planning/ directory; the request queue lives in %s" % QUESTIONS_REL)
    text = " ".join(args.text.split())
    if not text:
        raise BridgeError("empty question")
    ensure_requests_section(root)
    items = list_requests(root)
    dup = [r["id"] for r in items if r["text"].split("):", 1)[-1].split(" — ")[0].strip().lower() == text.lower()]
    if dup:
        raise BridgeError("the same question is already queued as %s" % ", ".join(dup))
    nums = [int(r["id"][3:]) for r in items]
    new_id = "SQ-%03d" % ((max(nums) if nums else 0) + 1)
    line = "- [ ] %s (%s, %s): %s" % (new_id, _dt.date.today().isoformat(), args.by, text)
    if args.context:
        line += " — context: %s" % args.context
    if args.affects:
        line += "; affects: %s" % args.affects if args.context else " — affects: %s" % args.affects
    path = _questions_path(root)
    lines = path.read_text(encoding="utf-8").splitlines()
    bounds = _section_bounds(lines)
    insert_at = bounds.stop if bounds else len(lines)
    while insert_at > 0 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _emit({"ok": True, "id": new_id, "line": line, "file": QUESTIONS_REL})
    return 0


def cmd_gsd_setup(args: argparse.Namespace) -> int:
    root = require_project(args.project_dir)
    agents = [a.strip() for a in args.agents.split(",") if a.strip()] if args.agents else list(GSD_DEFAULT_AGENTS)
    result = gsd_setup(root, agents)
    _emit({"ok": True, **result})
    return 0


# ---------------------------------------------------------------- export

def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _abs_patterns(patterns: List[Any]) -> List[str]:
    """The absolute export.allowed_sources patterns after ~ expansion, each also with its literal
    directory prefix resolved (so /tmp/x/*.csv matches the real path /private/tmp/x/... on macOS)."""
    out = []  # type: List[str]
    for pattern in patterns:
        p = os.path.expanduser(str(pattern))
        if not os.path.isabs(p):
            continue
        out.append(p)
        parts = Path(p).parts
        i = next((k for k, part in enumerate(parts) if any(c in part for c in "*?[")), len(parts))
        try:
            out.append(str(Path(*parts[:i]).resolve().joinpath(*parts[i:])))
        except (OSError, RuntimeError):
            pass
    return out


def _export_errors(root: Path, src: Path, rel: Optional[str], policy: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Check one export source. REL is its repo-relative path as named, or None when it is named outside
    the project. Returns (the manifest `source`: REL, or the absolute real path outside the repo; errors)."""
    export = policy["export"]
    try:
        real = src.resolve()
    except (OSError, RuntimeError):
        return rel or str(src), ["%s: cannot be resolved" % (rel or src)]
    if rel is None:
        label = str(real)
        if _within(real, root):
            return label, ["%s: resolves into the project; name the repo path instead" % src]
        if not any(fnmatch.fnmatchcase(label, p) for p in _abs_patterns(export["allowed_sources"])):
            return label, ["%s: outside the project %s and matches no absolute export.allowed_sources pattern "
                           "(e.g. \"~/shared/*.csv\")" % (label, root)]
    else:
        label = rel
        if not _within(real, root):
            return rel, ["%s: resolves outside the project" % rel]
        if policy["access"] == "repo-readonly":
            return rel, [EXPORT_REPO_READONLY % rel]
    if not real.is_file():
        return label, ["%s: not a regular file" % label]
    errors = []  # type: List[str]
    if rel is not None:
        named = {rel, real.relative_to(root).as_posix()}  # a symlink inside the repo is checked at both ends
        if any(fnmatch.fnmatchcase(r, p) for r in named for p in policy.get("do_not_read", [])):
            errors.append("%s: matches do_not_read in policy.json" % rel)
        if (root / "science") in real.parents:
            errors.append("%s: files already under science/ cannot be exported" % rel)
        patterns = export["allowed_sources"]
        if patterns and not any(fnmatch.fnmatchcase(rel, p) for p in patterns):
            errors.append("%s: does not match any export.allowed_sources pattern" % rel)
    exts = [e.lower() for e in export["allowed_extensions"]]
    if exts and real.suffix.lower() not in exts:
        errors.append("%s: extension %r is not in export.allowed_extensions" % (label, real.suffix))
    if real.stat().st_size > float(export["max_file_mb"]) * 1024 * 1024:
        errors.append("%s: larger than export.max_file_mb=%s" % (label, export["max_file_mb"]))
    cmd = export.get("check_command")
    if cmd and not errors:
        argv = [tok.replace("{file}", str(real)) for tok in shlex.split(cmd)]
        if not any("{file}" in tok for tok in shlex.split(cmd)):
            argv.append(str(real))
        try:
            proc = subprocess.run(argv, cwd=str(root), capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as e:
            errors.append("%s: check_command could not run: %s" % (label, e))
        else:
            if proc.returncode != 0:
                tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
                errors.append("%s: check_command refused it (exit %d): %s" % (label, proc.returncode, " | ".join(tail)))
    return label, errors


def cmd_export(args: argparse.Namespace) -> int:
    root = require_project(args.project_dir)
    policy = load_policy(root, for_export=True)
    manifest = load_manifest(root)
    outbox = root / "science" / "outbox"
    by_name = {f["name"]: f for f in manifest["files"]}

    plan = []
    all_errors = []  # type: List[str]
    seen = set()
    for raw in args.paths:
        src = Path(raw).expanduser()
        src = src if src.is_absolute() else (root / src)
        # Resolve directory symlinks (e.g. /var -> /private/var on macOS) but keep the final
        # component as written, so allowed_sources matches the path the user named; the
        # escape check in _export_errors resolves the full path separately.
        try:
            named = src.parent.resolve() / src.name
        except (OSError, RuntimeError) as e:
            all_errors.append("%s: cannot be resolved: %s" % (raw, e))
            continue
        rel = named.relative_to(root).as_posix() if _within(named, root) else None
        source, errs = _export_errors(root, src, rel, policy)
        name = src.name
        if name in OUTBOX_RESERVED:
            errs.append("%s: reserved name" % source)
        if name in seen:
            errs.append("%s: two files named %r in one export" % (source, name))
        seen.add(name)
        existing = by_name.get(name)
        if existing and existing["source"] != source:
            old_copy = policy["access"] == "repo-readonly" and not os.path.isabs(existing["source"])
            errs.append("%s: outbox already has %r from %s; %s" % (
                source, name, existing["source"],
                "it is an old copy of a repo file, run outbox-clean to remove it" if old_copy else "rename one of them"))
        if errs:
            all_errors.extend(errs)
        else:
            plan.append((src.resolve(), source, name))

    if all_errors:
        _emit({"ok": False, "exported": [], "errors": all_errors,
               "note": "nothing was exported; fix every error or drop those paths, then re-run"})
        return 1

    exported = []
    for real, source, name in plan:
        digest = _sha256(real)
        entry = by_name.get(name)
        if entry and entry["sha256"] == digest and (outbox / name).is_file() and _sha256(outbox / name) == digest:
            exported.append({"name": name, "source": source, "status": "unchanged"})
            continue
        shutil.copyfile(str(real), str(outbox / name))
        new = {"name": name, "sha256": digest, "bytes": real.stat().st_size, "source": source, "exported_at": _now()}
        by_name[name] = new
        exported.append({"name": name, "source": source, "status": "updated" if entry else "added"})
    manifest["files"] = sorted(by_name.values(), key=lambda f: f["name"])
    manifest["policy_version"] = policy.get("policy_version")
    manifest["privacy"] = policy["privacy"]
    save_manifest(root, manifest)
    _emit({"ok": True, "exported": exported, "errors": []})
    return 0


# ---------------------------------------------------------------- check / status

def _outbox_report(root: Path, access: Optional[str] = None) -> Dict[str, Any]:
    """Outbox integrity. Under ACCESS "repo-readonly", entries copied from repo files (a relative manifest
    `source`) are listed in `redundant`, not checked for staleness: Claude Science reads those files in place."""
    outbox = root / "science" / "outbox"
    violations = []  # type: List[str]
    try:
        manifest = load_manifest(root)
    except BridgeError as e:
        return {"violations": [str(e)], "stale": [], "redundant": [], "files": 0}
    listed = {f["name"]: f for f in manifest.get("files", [])}
    present = set()
    if outbox.is_dir():
        for p in sorted(outbox.iterdir()):
            if p.name in OUTBOX_RESERVED or p.name.endswith(".json.tmp"):
                continue
            if p.is_dir():
                violations.append("outbox/%s/: subdirectories are not allowed" % p.name)
                continue
            present.add(p.name)
            entry = listed.get(p.name)
            if entry is None:
                violations.append("outbox/%s: not in MANIFEST.json (was it copied by hand?)" % p.name)
            elif _sha256(p) != entry.get("sha256"):
                violations.append("outbox/%s: content differs from MANIFEST.json sha256" % p.name)
    stale = []  # type: List[str]
    redundant = []  # type: List[Dict[str, str]]
    for name, entry in listed.items():
        if name not in present:
            violations.append("outbox/%s: listed in MANIFEST.json but missing" % name)
        source = entry.get("source")
        if not isinstance(source, str) or not source:
            continue
        if access == "repo-readonly" and not os.path.isabs(source):
            redundant.append({"name": name, "source": source})
            continue
        src = Path(source) if os.path.isabs(source) else root / source
        try:
            changed = src.is_file() and _sha256(src) != entry.get("sha256")
        except OSError:  # e.g. an outside source that is no longer readable
            changed = False
        if changed:
            stale.append("outbox/%s: %s changed since export; re-run export to refresh" % (name, source))
    return {"violations": violations, "stale": stale, "redundant": redundant, "files": len(present)}


def _inbox_report(root: Path) -> Dict[str, Any]:
    inbox = root / "science" / "inbox"
    findings, attachments = [], []
    if inbox.is_dir():
        for p in sorted(inbox.iterdir()):
            if p.name in INBOX_IGNORED or p.is_dir():
                continue
            if p.suffix.lower() == ".md":
                findings.append(validate_finding(p))
            else:
                attachments.append(p.name)
    return {"findings": findings, "attachments": attachments}


def cmd_check(args: argparse.Namespace) -> int:
    root = require_project(args.project_dir)
    policy = load_policy(root)
    out = _outbox_report(root, policy["access"])
    inb = _inbox_report(root)
    invalid = [f for f in inb["findings"] if not f["ok"]]
    ok = not out["violations"] and not invalid
    _emit({"ok": ok, "outbox": out, "inbox": {"pending": len(inb["findings"]), "invalid": len(invalid),
                                               "findings": inb["findings"], "attachments": inb["attachments"]},
           "gsd": gsd_report(root)})
    return 0 if ok else 1


def cmd_status(args: argparse.Namespace) -> int:
    root = require_project(args.project_dir)
    policy = load_policy(root)
    out = _outbox_report(root, policy["access"])
    inb = _inbox_report(root)
    _emit({
        "project": str(root),
        "plugin_version": PLUGIN_VERSION,
        "skill_version": SKILL_VERSION,
        "policy_version": policy.get("policy_version"),
        "legacy_policy": policy["_legacy"],
        "skill_min_version": policy["skill_min_version"],
        "privacy": policy["privacy"],
        "outbox": out,
        "pending": [{"file": Path(f["file"]).name, "ok": f["ok"], "errors": f["errors"], **f["meta"]}
                    for f in inb["findings"]],
        "attachments": inb["attachments"],
        "gsd": gsd_report(root),
        "access": policy["access"],
        "do_not_read": policy["do_not_read"],
        "compute": policy["compute"],
        "requests_open": [r for r in list_requests(root) if r["open"]],
        "consult": {k: v for k, v in consult_report(root, policy, probe=False).items()
                    if k in ("auto", "wait_minutes", "ask_gate", "unsent", "sent_awaiting")},
        "selftest_pending": (root / "science" / "inbox" / SELFTEST_NAME).is_file(),
        "last_selftest": _last_selftest(root),
    })
    return 0


def cmd_outbox_clean(args: argparse.Namespace) -> int:
    root = require_project(args.project_dir)
    policy = load_policy(root)
    if policy["access"] != "repo-readonly":
        raise BridgeError("outbox-clean only applies to access \"repo-readonly\" (this project: %s); under "
                          "science-only the outbox copies are what Claude Science reads" % policy["access"])
    manifest = load_manifest(root)
    redundant = _outbox_report(root, policy["access"])["redundant"]
    if not args.yes:
        _emit({"ok": True, "would_remove": redundant,
               "note": "dry run; run outbox-clean --yes to delete these copies from science/outbox/ and MANIFEST.json"
               if redundant else "nothing to remove"})
        return 0
    outbox = root / "science" / "outbox"
    names = set(r["name"] for r in redundant)
    for name in sorted(names):
        p = outbox / name
        if Path(name).name == name and name not in OUTBOX_RESERVED and (p.is_file() or p.is_symlink()):
            p.unlink()
    if names:
        manifest["files"] = [f for f in manifest.get("files", []) if f.get("name") not in names]
        save_manifest(root, manifest)
    _emit({"ok": True, "removed": redundant, "kept": [f.get("name") for f in manifest.get("files", [])]})
    return 0


# ---------------------------------------------------------------- archive

def cmd_archive(args: argparse.Namespace) -> int:
    root = require_project(args.project_dir)
    inbox = root / "science" / "inbox"
    archive = root / "science" / "archive"
    archive.mkdir(exist_ok=True)
    src = inbox / Path(args.file).name
    if not src.is_file():
        raise BridgeError("science/inbox/%s not found" % Path(args.file).name)
    result = validate_finding(src)
    if not result["ok"] and args.route not in ("rejected", "manual"):
        raise BridgeError("finding is invalid (%s); only --route rejected or manual can archive it"
                          % "; ".join(result["errors"]))
    if (archive / src.name).exists():
        raise BridgeError("science/archive/%s already exists" % src.name)

    moved = [src.name]
    text = src.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        token = line.strip().lstrip("-").strip().strip("'\"")
        if token.startswith("inbox/"):
            att = inbox / token[len("inbox/"):]
            if att.is_file() and att.parent == inbox and not (archive / att.name).exists():
                shutil.move(str(att), str(archive / att.name))
                moved.append(att.name)
    shutil.move(str(src), str(archive / src.name))
    record = {"file": src.name, "route": args.route, "ref": args.ref, "routed_at": _now(), "moved": moved}
    answers = result["meta"].get("answers") if result["ok"] else None
    if answers and args.route != "rejected":
        ids = [str(a) for a in answers]
        if result["meta"].get("partial") is True:
            record["requests"] = note_progress(root, ids, "science/archive/" + src.name)
        else:
            record["requests"] = tick_requests(root, ids, "science/archive/" + src.name)
    with (archive / "routing.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    _emit({"ok": True, **record})
    return 0


# ---------------------------------------------------------------- session hint

def plugin_update_available() -> Optional[Tuple[str, str]]:
    """(newer version, marketplace name) if a known marketplace offers a newer science-bridge."""
    base = Path(os.environ.get("SCIENCE_BRIDGE_PLUGINS_DIR") or (Path.home() / ".claude" / "plugins"))
    try:
        known = json.loads((base / "known_marketplaces.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    best = None  # type: Optional[Tuple[str, str]]
    for mkt, entry in (known.items() if isinstance(known, dict) else []):
        try:
            loc = Path(entry["installLocation"])
            catalog = json.loads((loc / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
            for plugin in catalog.get("plugins") or []:
                if not isinstance(plugin, dict) or plugin.get("name") != "science-bridge":
                    continue
                source = plugin.get("source", "./")
                version = _plugin_version(loc / source) if isinstance(source, str) else None
                version = version or plugin.get("version")
                if version and vtuple(version) > vtuple(PLUGIN_VERSION) and (
                        best is None or vtuple(version) > vtuple(best[0])):
                    best = (str(version), str(mkt))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return best


def _hint_outbox(root: Path) -> Optional[str]:
    out = _outbox_report(root)
    if out["violations"]:
        return ("[science-bridge] WARNING: science/outbox has %d problem(s) and Claude Science may read unapproved "
                "files there. Run /science-bridge:bridge check before using Claude Science." % len(out["violations"]))
    return None


def _hint_plugin_update(root: Path) -> Optional[str]:
    update = plugin_update_available()
    if update is None:
        return None
    new, mkt = update
    return ("[science-bridge] v%s is available (this session runs v%s). Run: claude plugin marketplace update %s && "
            "claude plugin update science-bridge@%s — then restart Claude Code." % (new, PLUGIN_VERSION, mkt, mkt))


def _hint_legacy_policy(root: Path) -> Optional[str]:
    raw = _read_policy_raw(root / "science" / "policy.json")
    version = raw.get("policy_version")
    if version is None or (isinstance(version, int) and version < POLICY_VERSION):
        return ("[science-bridge] science/policy.json uses the old v%s format; run /science-bridge:bridge init to "
                "upgrade science/ (policy v%d)." % (version or 1, POLICY_VERSION))
    return None


def _hint_selftest(root: Path) -> Optional[str]:
    if (root / "science" / "inbox" / SELFTEST_NAME).is_file():
        return ("[science-bridge] Claude Science's self-test result is waiting in science/inbox/%s; run "
                "/science-bridge:bridge verify." % SELFTEST_NAME)
    last = _last_selftest(root)
    if last is None or vtuple(last.get("skill_version")) < vtuple(SKILL_VERSION):
        return ("[science-bridge] Claude Science hasn't confirmed skill v%s yet. Run /science-bridge:bridge cs-setup, "
                "republish the skill, then run the self-test." % SKILL_VERSION)
    return None


def _hint_findings(root: Path) -> Optional[str]:
    pending = _inbox_report(root)["findings"]
    if not pending:
        return None
    invalid = sum(1 for f in pending if not f["ok"])
    return ("[science-bridge] science/inbox has %d Claude Science finding(s) waiting%s. Run "
            "/science-bridge:bridge ingest to route them into GSD."
            % (len(pending), " (%d invalid)" % invalid if invalid else ""))


def _hint_requests(root: Path) -> Optional[str]:
    open_requests = [r for r in list_requests(root) if r["open"]]
    if not open_requests:
        return None
    if consult_settings(load_policy(root))["auto"]:
        unsent = consult_report(root, probe=False)["unsent"]
        if unsent:
            return ("[science-bridge] auto-consult is on and %d question(s) haven't been sent to Claude Science (%s). "
                    "Run /science-bridge:bridge consult to send them through Claude in Chrome."
                    % (len(unsent), ", ".join(u["id"] for u in unsent[:6])))
        return ("[science-bridge] %d question(s) were sent to Claude Science and await findings; "
                "/science-bridge:bridge consult shows them." % len(open_requests))
    return ("[science-bridge] %d open question(s) for Claude Science (%s). In Claude Science, ask it to process the "
            "open SQ requests." % (len(open_requests), QUESTIONS_REL))


def _hint_consult_rule(root: Path) -> Optional[str]:
    """The standing rule, printed at every session start (and after /clear or a compaction) while auto-consult is
    on: without it a fresh session only hears about Claude Science once something is already queued."""
    if not consult_settings(load_policy(root))["auto"]:
        return None
    return CONSULT_RULE_TEXT


def _hint_gsd(root: Path) -> Optional[str]:
    gsd = gsd_report(root)
    if gsd.get("planning") and gsd.get("skill") in ("missing", "outdated"):
        return ("[science-bridge] GSD agents' science-context skill is %s. Run /science-bridge:bridge gsd-setup so "
                "researchers/planners see Claude Science findings." % gsd["skill"])
    return None


def _hint_outbox_redundant(root: Path) -> Optional[str]:
    access = load_policy(root)["access"]
    if access != "repo-readonly":
        return None
    n = len(_outbox_report(root, access)["redundant"])
    if not n:
        return None
    return ("[science-bridge] science/outbox has %d old copies of repo files; Claude Science now reads the repo "
            "directly. Run /science-bridge:bridge outbox-clean to remove them." % n)


SESSION_HINTS = (_hint_outbox, _hint_plugin_update, _hint_legacy_policy, _hint_selftest, _hint_findings,
                 _hint_requests, _hint_consult_rule, _hint_gsd, _hint_outbox_redundant)


# ---------------------------------------------------------------- wait (watch the inbox)

def _inbox_snapshot(root: Path) -> Dict[str, Tuple[int, float]]:
    inbox = root / "science" / "inbox"
    snap = {}  # type: Dict[str, Tuple[int, float]]
    if inbox.is_dir():
        for p in inbox.iterdir():
            if p.is_file() and p.name not in (".gitkeep", "README.md", ".selftest-probe"):
                try:
                    st = p.stat()
                except OSError:
                    continue
                snap[p.name] = (st.st_size, st.st_mtime)
    return snap


def _arrivals(before: Dict[str, Tuple[int, float]], now: Dict[str, Tuple[int, float]]) -> List[str]:
    return sorted(n for n, sig in now.items() if before.get(n) != sig)


def cmd_wait(args: argparse.Namespace) -> int:
    """Block until Claude Science writes something to science/inbox/ (a finding or selftest.json), then wait
    until the folder has been quiet for --settle seconds so half-written files are not picked up.

    Claude Science has no API, so this watches files only. Run it in the background from Claude Code; exit 0 =
    something arrived (JSON lists it), exit 2 = timeout with nothing new (a good moment to look at Claude
    Science for an approval prompt or a question), exit 1 = error.
    """
    import time
    root = require_project(args.project_dir)
    interval = max(1.0, float(args.interval))
    deadline = time.time() + max(0.0, float(args.timeout))
    start = _inbox_snapshot(root)
    already = sorted(n for n in start if n.endswith(".md") or n == SELFTEST_NAME)
    if already and not args.new_only:
        changed = already
    else:
        changed = []
        while time.time() < deadline:
            time.sleep(interval)
            now = _inbox_snapshot(root)
            changed = [n for n in _arrivals(start, now) if n.endswith(".md") or n == SELFTEST_NAME]
            if changed:
                break
        if not changed:
            _emit({"ok": True, "arrived": [], "timed_out": True, "waited_s": int(args.timeout),
                   "hint": "Nothing new in science/inbox/. If Claude Science looks idle, check it for "
                           "'Waiting for your approval' or a question; if it answered only in chat, ask it to "
                           "write the answer as a finding in science/inbox/."})
            return 2
    # settle: wait until no file has changed for --settle seconds (bounded by the overall timeout + settle)
    last = _inbox_snapshot(root)
    quiet_since = time.time()
    settle_deadline = max(deadline, time.time()) + float(args.settle) * 6
    while time.time() - quiet_since < float(args.settle) and time.time() < settle_deadline:
        time.sleep(min(interval, max(0.5, float(args.settle) / 4)))
        now = _inbox_snapshot(root)
        if now != last:
            last, quiet_since = now, time.time()
    inb = _inbox_report(root)
    findings = [{"file": Path(f["file"]).name, "ok": f["ok"], "errors": f["errors"], **f["meta"]}
                for f in inb["findings"]]
    selftest = (root / "science" / "inbox" / SELFTEST_NAME).is_file()
    nxt = []
    if selftest:
        nxt.append("/science-bridge:bridge verify")
    if findings:
        nxt.append("/science-bridge:bridge ingest")
    _emit({"ok": True, "arrived": sorted(set(changed) | set(n for n in last if n not in start)),
           "timed_out": False, "findings": findings, "attachments": inb["attachments"],
           "selftest_waiting": selftest, "next": nxt})
    return 0


def cmd_session_hint(args: argparse.Namespace) -> int:
    try:
        start = None
        if not sys.stdin.isatty():
            try:
                payload = json.loads(sys.stdin.read() or "{}")
                start = payload.get("cwd")
            except (ValueError, AttributeError):
                start = None
        root = find_project(_start_dir(start or args.project_dir))
        if root is None:
            return 0
        lines = []
        for hint in SESSION_HINTS:
            try:
                line = hint(root)
            except Exception:  # one broken hint must not hide the others
                line = None
            if line:
                lines.append(line)
        if lines:
            print("\n".join(lines[:MAX_HINT_LINES]))
    except Exception:  # never break session start
        pass
    return 0


# ---------------------------------------------------------------- consult (auto-consult during GSD work)
#
# Claude Science has no API, so nothing here calls it. Auto-consult makes the *orchestrating* Claude Code session
# do the consult round itself (send the queued SQ items through Claude in Chrome, wait for the findings, ingest
# them) at the next checkpoint. The checkpoint is a PostToolUse hook on the Agent/Task/Skill tools: GSD workflows,
# /gsd-autonomous included, call one of them at every step, and the hook adds a reminder to the model's context
# when there is consult work. The round itself is described in skills/bridge/SKILL.md ("consult").
#
# That hook only sees questions that are already queued. Three more pieces make the session queue them:
# - the standing rule in the SessionStart hint (_hint_consult_rule);
# - the hand-back reminder, when a GSD agent returns a decision checkpoint or a blocked/inconclusive result.
#   Subagents usually run in the background, so PostToolUse on Agent fires when one is launched and its tool
#   response holds no report. SubagentStop (and PostToolUse on SubagentHandback, where the report goes through that
#   tool) sees the report instead. Its output would go to the subagent, so it only notes the hand-back in the
#   state file; the orchestrator is told at its next Agent/Task/Skill call or by the ask gate;
# - the ask gate, a PreToolUse hook on AskUserQuestion. A PreToolUse hook's additionalContext only reaches the model
#   with the tool result, after the owner has answered, so the gate refuses the call once instead.

def consult_settings(policy: Dict[str, Any]) -> Dict[str, Any]:
    block = policy.get("consult") if isinstance(policy.get("consult"), dict) else {}
    return {"auto": block.get("auto") is True, "wait_minutes": block.get("wait_minutes", CONSULT_DEFAULT_WAIT),
            "ask_gate": block.get("ask_gate") is not False}


def _consult_state_path(root: Path) -> Path:
    return root / CONSULT_STATE_REL


def load_consult_state(root: Path) -> Dict[str, Any]:
    try:
        raw = json.loads(_consult_state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    sent = raw.get("sent") if isinstance(raw.get("sent"), dict) else {}
    last = raw.get("last_hint") if isinstance(raw.get("last_hint"), dict) else {}
    gate = raw.get("last_gate") if isinstance(raw.get("last_gate"), dict) else {}
    handback = raw.get("handback") if isinstance(raw.get("handback"), dict) else {}
    return {"sent": {str(k): str(v) for k, v in sent.items()}, "last_hint": last, "last_gate": gate,
            "handback": handback}


def save_consult_state(root: Path, state: Dict[str, Any]) -> None:
    path = _consult_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def claude_science_status() -> Dict[str, Any]:
    """What `claude-science status` says, reduced to running/port/version/url. Never raises."""
    cli = os.environ.get(CS_CLI_ENV) or shutil.which("claude-science")
    if not cli or cli == "none":
        return {"cli": False, "running": None,
                "note": "claude-science is not on PATH; open Claude Science yourself and check it is running"}
    try:
        proc = subprocess.run([cli, "status"], capture_output=True, text=True, timeout=10)
        data = json.loads(proc.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"cli": True, "running": None, "note": "claude-science status gave no readable answer"}
    if not isinstance(data, dict):
        return {"cli": True, "running": None, "note": "claude-science status gave no readable answer"}
    running = data.get("running") is True
    port = data.get("port") if isinstance(data.get("port"), int) else 8765
    out = {"cli": True, "running": running, "version": data.get("version"), "url": CS_URL % port}
    if not running:
        out["note"] = "Claude Science is not running; start it with `claude-science serve`"
    return out


def consult_report(root: Path, policy: Optional[Dict[str, Any]] = None, probe: bool = True) -> Dict[str, Any]:
    policy = policy if policy is not None else load_policy(root)
    settings = consult_settings(policy)
    state = load_consult_state(root)
    open_items = [r for r in list_requests(root) if r["open"]]
    unsent = [{"id": r["id"], "text": r["text"]} for r in open_items if r["id"] not in state["sent"]]
    sent = [{"id": r["id"], "sent_at": state["sent"][r["id"]]} for r in open_items if r["id"] in state["sent"]]
    pending = [Path(f["file"]).name for f in _inbox_report(root)["findings"]]
    if pending:
        nxt = "ingest the findings waiting in science/inbox/ (/science-bridge:bridge ingest --yes)"
    elif unsent:
        nxt = "send %s to Claude Science (the consult round in the bridge skill)" % ", ".join(u["id"] for u in unsent)
    elif sent:
        nxt = "wait for Claude Science's findings (bridge.py wait --new-only)"
    else:
        nxt = "nothing to do"
    out = {"auto": settings["auto"], "wait_minutes": settings["wait_minutes"], "ask_gate": settings["ask_gate"],
           "unsent": unsent,
           "sent_awaiting": sent, "pending_findings": pending, "next": nxt}
    if probe:
        out["claude_science"] = claude_science_status()
    return out


def _write_consult_block(root: Path, auto: bool, wait: Optional[float],
                         ask_gate: Optional[bool] = None) -> Dict[str, Any]:
    path = root / "science" / "policy.json"
    raw = _read_policy_raw(path)
    block = dict(raw.get("consult")) if isinstance(raw.get("consult"), dict) else {}
    block["auto"] = auto
    if ask_gate is not None:
        block["ask_gate"] = ask_gate
    if wait is not None:
        block["wait_minutes"] = int(wait) if float(wait).is_integer() else float(wait)
    block.setdefault("wait_minutes", CONSULT_DEFAULT_WAIT)
    new = dict(raw)
    new["consult"] = block
    normalize_policy(new)
    _write_text_atomic(path, _json_pretty(new) + "\n")
    return block


def _consult_hint_text(report: Dict[str, Any]) -> str:
    parts = []
    if report["unsent"]:
        shown = []
        for item in report["unsent"][:5]:
            text = item["text"].split("):", 1)[-1].split(" — ")[0].strip()
            shown.append("%s (%s)" % (item["id"], text[:90] + ("…" if len(text) > 90 else "")))
        more = " and %d more" % (len(report["unsent"]) - 5) if len(report["unsent"]) > 5 else ""
        parts.append("%d question(s) for Claude Science are queued but not sent yet: %s%s."
                     % (len(report["unsent"]), "; ".join(shown), more))
    if report["pending_findings"]:
        parts.append("%d Claude Science finding(s) are waiting in science/inbox/." % len(report["pending_findings"]))
    return ("%s %s Auto-consult is on for this project, so before you start the next step, run the consult round "
            "(the \"consult\" section of the science-bridge bridge skill, /science-bridge:bridge consult): ingest "
            "waiting findings with --yes, send unsent questions to Claude Science through Claude in Chrome, and "
            "wait up to %s minutes for the answers before continuing work that depends on them. If you can't use "
            "Claude in Chrome here, don't try: say once that the questions stay queued, and continue."
            % (CONSULT_TAG, " ".join(parts), report["wait_minutes"]))


def _payload_text(value: Any, limit: int = 400000) -> str:
    """Every string inside a hook payload value, one per line (tool inputs and responses come as str, dict or list)."""
    parts = []  # type: List[str]

    def walk(v: Any) -> None:
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            for item in v.values():
                walk(item)
        elif isinstance(v, (list, tuple)):
            for item in v:
                walk(item)

    walk(value)
    return "\n".join(parts)[:limit]


def _handback_report(payload: Dict[str, Any]) -> Optional[str]:
    """The report a subagent hands back, from the two events that carry it; None for every other event."""
    event = payload.get("hook_event_name")
    if event == "SubagentStop":
        return _payload_text(payload.get("last_assistant_message"))
    if event == "PostToolUse" and payload.get("tool_name") == "SubagentHandback":
        tool_input = payload.get("tool_input")
        return _payload_text(tool_input.get("message") if isinstance(tool_input, dict) else tool_input)
    return None


def _handback_kind(text: str) -> Optional[str]:
    """What a GSD agent's report hands back to the orchestrator, if it is a decision or a blocker."""
    if GSD_CHECKPOINT_RE.search(text) and GSD_CHECKPOINT_DECISION_RE.search(text):
        return "a decision checkpoint"
    if GSD_STUCK_RE.search(text):
        return "a blocked or inconclusive result"
    return None


def _due_reminders(root: Path, policy: Dict[str, Any], state: Dict[str, Any], now: _dt.datetime) -> List[str]:
    """The reminders the orchestrating session hasn't had yet: queued consult work (not repeated unchanged within
    CONSULT_REHINT_MINUTES) and a noted hand-back (once). Marks them as given in STATE; the caller saves it."""
    texts = []  # type: List[str]
    report = consult_report(root, policy, probe=False)
    if report["unsent"] or report["pending_findings"]:
        key = hashlib.sha256(json.dumps([[u["id"] for u in report["unsent"]], report["pending_findings"]])
                             .encode("utf-8")).hexdigest()[:16]
        last_at = _parse_utc(state["last_hint"].get("at"))
        if not (state["last_hint"].get("key") == key and last_at is not None and
                (now - last_at).total_seconds() < CONSULT_REHINT_MINUTES * 60):
            state["last_hint"] = {"key": key, "at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
            texts.append(_consult_hint_text(report))
    handback = state["handback"]
    if handback:
        state["handback"] = {}
        noted_at = _parse_utc(handback.get("at"))
        if noted_at is not None and (now - noted_at).total_seconds() < CONSULT_HANDBACK_MAX_MINUTES * 60:
            agent = str(handback.get("agent") or "")
            texts.append(CONSULT_HANDBACK_TEXT % ("A subagent (%s)" % agent if agent else "A subagent",
                                                  handback.get("what") or "a decision or a blocker"))
    return texts


def _gate_is_due(state: Dict[str, Any], question: str, key: str, now: _dt.datetime) -> bool:
    """Whether the ask gate should refuse this question on its own account. Not when the question names an SQ id,
    within CONSULT_REHINT_MINUTES of the last refusal, or for the refused question itself (CONSULT_GATE_SAME_HOURS)."""
    if SQ_ID_RE.search(question):
        return False
    last_at = _parse_utc(state["last_gate"].get("at"))
    if last_at is None:
        return True
    age = (now - last_at).total_seconds()
    if age < CONSULT_REHINT_MINUTES * 60:
        return False
    return not (state["last_gate"].get("key") == key and age < CONSULT_GATE_SAME_HOURS * 3600)


def consult_hook(payload: Dict[str, Any], now: Optional[_dt.datetime] = None) -> Optional[Dict[str, Any]]:
    """The hook body for every registered event (see hooks/hooks.json). Returns the hook's JSON output, or None to
    stay silent."""
    root = find_project(_start_dir(payload.get("cwd") or None))
    if root is None:
        return None
    policy = load_policy(root)
    settings = consult_settings(policy)
    if not settings["auto"]:
        return None
    now = now or _dt.datetime.now(_dt.timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    handed_back = _handback_report(payload)
    if handed_back is not None:  # runs inside the subagent: note it for the orchestrator, say nothing here
        kind = _handback_kind(handed_back)
        if kind:
            state = load_consult_state(root)
            state["handback"] = {"what": kind, "agent": str(payload.get("agent_type") or ""), "at": stamp}
            save_consult_state(root, state)
        return None
    if payload.get("agent_id"):
        return None  # inside a subagent: it can't drive Chrome; the main session gets the reminder next
    event = payload.get("hook_event_name") or "PostToolUse"
    gate = event == "PreToolUse"
    if gate and (payload.get("tool_name") != "AskUserQuestion" or not settings["ask_gate"]):
        return None
    state = load_consult_state(root)
    before = json.dumps(state, sort_keys=True)
    texts = _due_reminders(root, policy, state, now)
    out = None  # type: Optional[Dict[str, Any]]
    if gate:
        question = _payload_text(payload.get("tool_input"))
        key = hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]
        if texts or _gate_is_due(state, question, key, now):
            state["last_gate"] = {"key": key, "at": stamp}
            out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                          "permissionDecisionReason": "\n".join(texts + [CONSULT_GATE_TEXT])}}
    elif texts:
        out = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": "\n".join(texts)}}
    if json.dumps(state, sort_keys=True) != before:
        save_consult_state(root, state)
    return out


def cmd_consult(args: argparse.Namespace) -> int:
    if args.action == "hook":
        try:
            payload = {}  # type: Dict[str, Any]
            if not sys.stdin.isatty():
                try:
                    loaded = json.loads(sys.stdin.read() or "{}")
                    payload = loaded if isinstance(loaded, dict) else {}
                except ValueError:
                    payload = {}
            if not payload.get("cwd") and args.project_dir:
                payload["cwd"] = args.project_dir
            out = consult_hook(payload)
            if out:
                print(json.dumps(out, ensure_ascii=False))
        except Exception:  # a hook must never break the tool call it follows
            pass
        return 0
    root = require_project(args.project_dir)
    if args.action in ("on", "off"):
        if args.ids:
            raise BridgeError("consult %s takes no SQ ids" % args.action)
        gate = None if args.ask_gate is None else args.ask_gate == "on"
        block = _write_consult_block(root, args.action == "on", args.wait_minutes, gate)
        note = ("Auto-consult is on. At each GSD step Claude Code is reminded to send new SQ questions to Claude "
                "Science through Claude in Chrome, wait up to %s minutes for the findings, and ingest them with the "
                "default routes (conflicts still go to you). It is also told to queue hard problems and decisions "
                "itself: at session start, when a GSD agent hands back a decision or a blocker, and %s. Needs "
                "Claude Code started with Chrome and Claude Science running. Commit science/policy.json."
                % (block["wait_minutes"],
                   "before it asks you a question (the ask gate refuses the first attempt once)"
                   if block.get("ask_gate") is not False else "not before it asks you a question (ask gate off)")
                if block["auto"] else "Auto-consult is off. Questions queue up for the next manual round.")
        _emit({"ok": True, "consult": block, "note": note, "report": consult_report(root)})
        return 0
    if args.ask_gate is not None:
        raise BridgeError("--ask-gate goes with `consult on` or `consult off`")
    if args.action == "sent":
        if not args.ids:
            raise BridgeError("consult sent needs the SQ ids you handed to Claude Science, e.g. SQ-004 SQ-005")
        items = {r["id"]: r for r in list_requests(root)}
        state = load_consult_state(root)
        recorded, closed, unknown = [], [], []
        for sq in args.ids:
            sq = sq.strip().upper()
            if sq not in items:
                unknown.append(sq)
            elif not items[sq]["open"]:
                closed.append(sq)
            else:
                state["sent"][sq] = _now()
                recorded.append(sq)
        if recorded:
            save_consult_state(root, state)
        _emit({"ok": not unknown, "recorded": recorded, "already_answered": closed, "unknown": unknown,
               "next": "start `wait --new-only` in the background" if recorded else None})
        return 0 if not unknown else 1
    if args.ids:
        raise BridgeError("consult status takes no SQ ids")
    _emit({"ok": True, **consult_report(root)})
    return 0


# ---------------------------------------------------------------- main

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="bridge.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--project-dir", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--no-gsd", action="store_true", help="don't run gsd-setup even if .planning/ exists")
    p = sub.add_parser("cs-setup")
    p.add_argument("--out", default=None, help="where to write the skill zip (default: temp dir)")
    p = sub.add_parser("skill-zip")
    p.add_argument("--out", default=None, help="zip path (default: <tmp>/science-bridge/science-findings-<ver>.zip)")
    p = sub.add_parser("verify")
    p.add_argument("--max-age-hours", type=float, default=24.0, help="oldest acceptable self-test (default 24)")
    sub.add_parser("compute-check")
    p = sub.add_parser("gsd-setup")
    p.add_argument("--agents", default=None, help="comma-separated GSD agent types (default: researchers + planners)")
    p = sub.add_parser("export")
    p.add_argument("paths", nargs="+")
    p = sub.add_parser("outbox-clean")
    p.add_argument("--yes", action="store_true", help="delete the copies (default: dry run, list them)")
    sub.add_parser("check")
    sub.add_parser("status")
    p = sub.add_parser("archive")
    p.add_argument("file")
    p.add_argument("--route", required=True, choices=ROUTES)
    p.add_argument("--ref", required=True, help="where it went, e.g. .planning/notes/2026-11-06-x.md")
    p = sub.add_parser("requests")
    p.add_argument("--all", action="store_true", help="include answered items")
    p = sub.add_parser("ask")
    p.add_argument("text")
    p.add_argument("--by", default="user", help="who is asking, e.g. 'gsd-planner phase 3'")
    p.add_argument("--context", default=None, help="paths the question concerns")
    p.add_argument("--affects", default=None, help="requirement IDs, e.g. 'LBL-01, LBL-06'")
    sub.add_parser("session-hint")
    p = sub.add_parser("wait")
    p.add_argument("--timeout", type=float, default=3600, help="seconds to wait for something new (default 3600)")
    p.add_argument("--interval", type=float, default=10, help="poll interval in seconds (default 10)")
    p.add_argument("--settle", type=float, default=20, help="quiet seconds before reporting (default 20)")
    p.add_argument("--new-only", action="store_true", help="ignore findings already waiting at start")
    p = sub.add_parser("consult")
    p.add_argument("action", nargs="?", default="status", choices=("status", "on", "off", "sent", "hook"))
    p.add_argument("ids", nargs="*", help="for `sent`: the SQ ids handed to Claude Science")
    p.add_argument("--wait-minutes", type=float, default=None,
                   help="for `on`: how long a consult round waits for findings (default %d)" % CONSULT_DEFAULT_WAIT)
    p.add_argument("--ask-gate", choices=("on", "off"), default=None,
                   help="for `on`/`off`: whether the first AskUserQuestion is refused once so Claude Science is "
                        "consulted before the owner is asked (default on)")
    args = ap.parse_args(argv)
    handlers = {"init": cmd_init, "export": cmd_export, "check": cmd_check, "status": cmd_status,
                "archive": cmd_archive, "session-hint": cmd_session_hint, "gsd-setup": cmd_gsd_setup,
                "requests": cmd_requests, "ask": cmd_ask, "cs-setup": cmd_cs_setup, "skill-zip": cmd_skill_zip,
                "verify": cmd_verify, "compute-check": cmd_compute_check, "outbox-clean": cmd_outbox_clean, "wait": cmd_wait,
                "consult": cmd_consult}
    try:
        return handlers[args.cmd](args)
    except BridgeError as e:
        _emit({"ok": False, "error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
