#!/usr/bin/env python3
"""science-bridge CLI, the Claude Code side of the Claude Code <-> Claude Science handoff.

Standard library only, Python 3.8+. The handoff format is defined in
skills/science-findings/references/format.md.

Commands:
  init [--project-dir DIR]          create science/{outbox,inbox,archive}, README.md, policy.json
                                    (never overwrites existing files)
  export PATH [PATH ...]            copy approved files into science/outbox/ and record them in
                                    MANIFEST.json; refuses anything policy.json does not allow
  check                             verify outbox integrity against MANIFEST.json and validate
                                    inbox findings; exit 1 on any problem
  status                            list pending inbox findings with validation results (JSON)
  archive FILE --route R --ref TXT  move a routed finding (and its inbox attachments) to
                                    science/archive/ and append to archive/routing.jsonl
  gsd-setup [--agents a,b,...]      GSD integration: install the science-context skill at
                                    .claude/skills/science-context/ and add it to
                                    .planning/config.json agent_skills for researcher/planner
                                    agents (merge only; existing entries are kept). `init` runs
                                    this automatically when .planning/config.json exists.
  session-hint                      for the SessionStart hook: print a one-line reminder when
                                    there is something to act on; otherwise print nothing.
                                    Always exits 0.

Every command except init locates the project by walking up from --project-dir
(default: $CLAUDE_PROJECT_DIR or the current directory) to the first directory that
contains science/policy.json.
"""

import argparse
import datetime as _dt
import fnmatch
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = PLUGIN_ROOT / "templates"
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "science-findings" / "scripts"))
from check_finding import validate_finding  # noqa: E402

GSD_SKILL_TEMPLATE = TEMPLATES / "gsd" / "science-context" / "SKILL.md"
GSD_SKILL_ENTRY = ".claude/skills/science-context"
GSD_DEFAULT_AGENTS = ("gsd-phase-researcher", "gsd-project-researcher", "gsd-advisor-researcher",
                      "gsd-planner", "gsd-plan-checker", "gsd-roadmapper")
MANIFEST_FORMAT = "science-bridge-manifest/v0.1"
PRIVACY_LEVELS = ("phi", "confidential", "public")
ROUTES = ("note", "todo", "seed", "manual", "rejected")
OUTBOX_RESERVED = {"MANIFEST.json", ".gitkeep"}
INBOX_IGNORED = {".gitkeep", "README.md"}


class BridgeError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _emit(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


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


def load_policy(root: Path, for_export: bool = False) -> Dict[str, Any]:
    path = root / "science" / "policy.json"
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BridgeError("cannot read %s: %s" % (path, e))
    privacy = policy.get("privacy")
    if privacy not in PRIVACY_LEVELS:
        raise BridgeError("policy.json: privacy must be one of %s" % ", ".join(PRIVACY_LEVELS))
    policy.setdefault("allowed_sources", [])
    policy.setdefault("allowed_extensions", [])
    policy.setdefault("check_command", None)
    policy.setdefault("max_file_mb", 50)
    if for_export and privacy == "phi" and not policy["allowed_sources"]:
        raise BridgeError("policy.json: privacy 'phi' requires a non-empty allowed_sources whitelist")
    return policy


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

def cmd_init(args: argparse.Namespace) -> int:
    root = _start_dir(args.project_dir)
    sci = root / "science"
    created: List[str] = []
    for sub in ("outbox", "inbox", "archive"):
        d = sci / sub
        if not d.exists():
            d.mkdir(parents=True)
            created.append(str(d.relative_to(root)) + "/")
        keep = d / ".gitkeep"
        if not keep.exists() and sub != "outbox":
            keep.write_text("")
    for name in ("README.md", "policy.json"):
        dst = sci / name
        if not dst.exists():
            shutil.copyfile(str(TEMPLATES / name), str(dst))
            created.append(str(dst.relative_to(root)))
    manifest = sci / "outbox" / "MANIFEST.json"
    if not manifest.exists():
        save_manifest(root, {"format": MANIFEST_FORMAT, "files": []})
        created.append(str(manifest.relative_to(root)))
    gsd = None
    if not args.no_gsd and (root / ".planning" / "config.json").is_file():
        gsd = gsd_setup(root, list(GSD_DEFAULT_AGENTS))
    _emit({
        "ok": True,
        "project": str(root),
        "created": created,
        "gsd": gsd if gsd is not None else "skipped (no .planning/config.json or --no-gsd)",
        "next_steps": [
            "Edit science/policy.json (privacy level, allowed_sources, check_command).",
            "Edit science/README.md (project brief for Claude Science).",
            "In Claude Science: grant ONLY the science/ folder, and import the science-findings skill.",
        ],
    })
    return 0


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

    verified: Optional[bool] = None
    sdk = shutil.which("gsd-sdk")
    if sdk and agents:
        try:
            proc = subprocess.run([sdk, "query", "agent-skills", agents[0]], cwd=str(root),
                                  capture_output=True, text=True, timeout=60)
            verified = GSD_SKILL_ENTRY + "/SKILL.md" in proc.stdout
        except (OSError, subprocess.TimeoutExpired):
            verified = None
    return {"skill": "%s/SKILL.md (%s)" % (GSD_SKILL_ENTRY, skill_status), "agents_added": added,
            "agents": agents, "gsd_sdk_verified": verified}


def cmd_gsd_setup(args: argparse.Namespace) -> int:
    root = require_project(args.project_dir)
    agents = [a.strip() for a in args.agents.split(",") if a.strip()] if args.agents else list(GSD_DEFAULT_AGENTS)
    result = gsd_setup(root, agents)
    _emit({"ok": True, **result})
    return 0


# ---------------------------------------------------------------- export

def _export_errors(root: Path, src: Path, rel: str, policy: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    try:
        real = src.resolve()
        real.relative_to(root)
    except (OSError, ValueError):
        return ["%s: resolves outside the project" % rel]
    if not real.is_file():
        return ["%s: not a regular file" % rel]
    if (root / "science") in real.parents:
        errors.append("%s: files already under science/ cannot be exported" % rel)
    exts = [e.lower() for e in policy["allowed_extensions"]]
    if exts and real.suffix.lower() not in exts:
        errors.append("%s: extension %r is not in allowed_extensions" % (rel, real.suffix))
    if real.stat().st_size > float(policy["max_file_mb"]) * 1024 * 1024:
        errors.append("%s: larger than max_file_mb=%s" % (rel, policy["max_file_mb"]))
    patterns = policy["allowed_sources"]
    if patterns and not any(fnmatch.fnmatchcase(rel, p) for p in patterns):
        errors.append("%s: does not match any allowed_sources pattern" % rel)
    cmd = policy.get("check_command")
    if cmd and not errors:
        argv = [tok.replace("{file}", str(real)) for tok in shlex.split(cmd)]
        if not any("{file}" in tok for tok in shlex.split(cmd)):
            argv.append(str(real))
        try:
            proc = subprocess.run(argv, cwd=str(root), capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as e:
            errors.append("%s: check_command could not run: %s" % (rel, e))
        else:
            if proc.returncode != 0:
                tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
                errors.append("%s: check_command refused it (exit %d): %s" % (rel, proc.returncode, " | ".join(tail)))
    return errors


def cmd_export(args: argparse.Namespace) -> int:
    root = require_project(args.project_dir)
    policy = load_policy(root, for_export=True)
    manifest = load_manifest(root)
    outbox = root / "science" / "outbox"
    by_name = {f["name"]: f for f in manifest["files"]}

    plan = []
    all_errors: List[str] = []
    seen = set()
    for raw in args.paths:
        src = Path(raw)
        src = src if src.is_absolute() else (root / src)
        # Resolve directory symlinks (e.g. /var -> /private/var on macOS) but keep the final
        # component as written, so allowed_sources matches the path the user named; the
        # escape check in _export_errors resolves the full path separately.
        try:
            rel = (src.parent.resolve() / src.name).relative_to(root).as_posix()
        except (OSError, ValueError):
            all_errors.append("%s: outside the project %s" % (raw, root))
            continue
        errs = _export_errors(root, src, rel, policy)
        name = src.name
        if name in OUTBOX_RESERVED:
            errs.append("%s: reserved name" % rel)
        if name in seen:
            errs.append("%s: two files named %r in one export" % (rel, name))
        seen.add(name)
        existing = by_name.get(name)
        if existing and existing["source"] != rel:
            errs.append("%s: outbox already has %r from %s; rename one of them" % (rel, name, existing["source"]))
        if errs:
            all_errors.extend(errs)
        else:
            plan.append((src.resolve(), rel, name))

    if all_errors:
        _emit({"ok": False, "exported": [], "errors": all_errors,
               "note": "nothing was exported; fix every error or drop those paths, then re-run"})
        return 1

    exported = []
    for real, rel, name in plan:
        digest = _sha256(real)
        entry = by_name.get(name)
        if entry and entry["sha256"] == digest and (outbox / name).is_file() and _sha256(outbox / name) == digest:
            exported.append({"name": name, "source": rel, "status": "unchanged"})
            continue
        shutil.copyfile(str(real), str(outbox / name))
        new = {"name": name, "sha256": digest, "bytes": real.stat().st_size, "source": rel, "exported_at": _now()}
        by_name[name] = new
        exported.append({"name": name, "source": rel, "status": "updated" if entry else "added"})
    manifest["files"] = sorted(by_name.values(), key=lambda f: f["name"])
    manifest["policy_version"] = policy.get("policy_version")
    manifest["privacy"] = policy["privacy"]
    save_manifest(root, manifest)
    _emit({"ok": True, "exported": exported, "errors": []})
    return 0


# ---------------------------------------------------------------- check / status

def _outbox_report(root: Path) -> Dict[str, Any]:
    outbox = root / "science" / "outbox"
    violations: List[str] = []
    try:
        manifest = load_manifest(root)
    except BridgeError as e:
        return {"violations": [str(e)], "files": 0}
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
    stale: List[str] = []
    for name, entry in listed.items():
        if name not in present:
            violations.append("outbox/%s: listed in MANIFEST.json but missing" % name)
        src = root / entry.get("source", "")
        if entry.get("source") and src.is_file() and _sha256(src) != entry.get("sha256"):
            stale.append("outbox/%s: %s changed since export; re-run export to refresh" % (name, entry["source"]))
    return {"violations": violations, "stale": stale, "files": len(present)}


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
    load_policy(root)
    out = _outbox_report(root)
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
    out = _outbox_report(root)
    inb = _inbox_report(root)
    _emit({
        "project": str(root),
        "privacy": policy["privacy"],
        "outbox": out,
        "pending": [{"file": Path(f["file"]).name, "ok": f["ok"], "errors": f["errors"], **f["meta"]}
                    for f in inb["findings"]],
        "attachments": inb["attachments"],
        "gsd": gsd_report(root),
    })
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
    with (archive / "routing.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    _emit({"ok": True, **record})
    return 0


# ---------------------------------------------------------------- session hint

def cmd_session_hint(args: argparse.Namespace) -> int:
    try:
        start = None
        if not sys.stdin.isatty():
            try:
                payload = json.loads(sys.stdin.read() or "{}")
                start = payload.get("cwd")
            except ValueError:
                start = None
        root = find_project(_start_dir(start or args.project_dir))
        if root is None:
            return 0
        out = _outbox_report(root)
        inb = _inbox_report(root)
        lines = []
        if out["violations"]:
            lines.append("[science-bridge] WARNING: science/outbox has %d problem(s) and Claude Science may read "
                         "unapproved files there. Run /science-bridge:bridge check before using Claude Science."
                         % len(out["violations"]))
        pending = inb["findings"]
        if pending:
            invalid = sum(1 for f in pending if not f["ok"])
            lines.append("[science-bridge] science/inbox has %d Claude Science finding(s) waiting%s. "
                         "Run /science-bridge:bridge ingest to route them into GSD."
                         % (len(pending), " (%d invalid)" % invalid if invalid else ""))
        gsd = gsd_report(root)
        if gsd.get("planning") and gsd.get("skill") in ("missing", "outdated"):
            lines.append("[science-bridge] GSD agents' science-context skill is %s. Run "
                         "/science-bridge:bridge gsd-setup so researchers/planners see Claude Science findings."
                         % gsd["skill"])
        if lines:
            print("\n".join(lines))
    except Exception:  # never break session start
        pass
    return 0


# ---------------------------------------------------------------- main

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="bridge.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--project-dir", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--no-gsd", action="store_true", help="don't run gsd-setup even if .planning/ exists")
    p = sub.add_parser("gsd-setup")
    p.add_argument("--agents", default=None, help="comma-separated GSD agent types (default: researchers + planners)")
    p = sub.add_parser("export")
    p.add_argument("paths", nargs="+")
    sub.add_parser("check")
    sub.add_parser("status")
    p = sub.add_parser("archive")
    p.add_argument("file")
    p.add_argument("--route", required=True, choices=ROUTES)
    p.add_argument("--ref", required=True, help="where it went, e.g. .planning/notes/2026-11-06-x.md")
    sub.add_parser("session-hint")
    args = ap.parse_args(argv)
    handlers = {"init": cmd_init, "export": cmd_export, "check": cmd_check, "status": cmd_status,
                "archive": cmd_archive, "session-hint": cmd_session_hint, "gsd-setup": cmd_gsd_setup}
    try:
        return handlers[args.cmd](args)
    except BridgeError as e:
        _emit({"ok": False, "error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
