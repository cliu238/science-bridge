#!/usr/bin/env python3
"""science-bridge self-test. Claude Science runs it inside its sandbox.

Standard library only, Python 3.8+. It records what this Claude Science project can see and do
in a science-bridge repo and writes the result to <repo-root>/science/inbox/selftest.json, which
`/science-bridge:bridge verify` checks on the Claude Code side.

Usage:
    python3 scripts/selftest.py <repo-root> [--compute NAME=ok|failed|skipped[:detail] ...]

--compute reports the result of testing one policy.json compute host with reach
"claude-science" (repeat it per host).

Guarantees:
  - never opens a path that matches policy.json "do_not_read" (when policy.json cannot be read,
    it reads nothing else in the repo);
  - writes only science/inbox/selftest.json plus two probe files that it deletes again:
    <repo-root>/.science-bridge-write-probe (creating it should be refused) and
    science/inbox/.selftest-probe;
  - never raises: every problem is recorded in the JSON.

Prints the JSON. Exit code: 0 if selftest.json was written, 1 if not, 2 on usage error.
"""

import datetime as _dt
import errno
import fnmatch
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

FORMAT = "science-bridge-selftest/v1"
SKILL_VERSION_RE = re.compile(r"^Skill version: (\d+\.\d+\.\d+)\s*$", re.MULTILINE)
REQUEST_LINE_RE = re.compile(r"^- \[( |x|X)\] (SQ-(\d{3,}))\b")  # same as bridge.py
REQUESTS_HEADING = "## For Claude Science"
QUESTIONS_REL = ".planning/research/questions.md"
WS_DIR_REL = ".planning/workstreams"  # same as bridge.py
WS_QUEUE_DEFAULT = ".planning/workstreams/{ws}/questions.md"
WS_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PROBE_REL = ".planning/PROJECT.md"
ROOT_PROBE = ".science-bridge-write-probe"
INBOX_PROBE = ".selftest-probe"
HOST_RE = re.compile(r"^[A-Za-z0-9._-]+$")
COMPUTE_RESULTS = ("ok", "failed", "skipped")
DENIED_ERRNOS = {errno.EACCES, errno.EPERM, errno.EROFS}
GIT_TIMEOUT = 30


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _err(e: BaseException) -> str:
    return "%s: %s" % (type(e).__name__, e)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Blocker:
    """do_not_read matcher. A path is blocked if it, or any directory above it, matches a pattern."""

    def __init__(self, patterns: List[str]):
        self.patterns = [p for p in patterns if isinstance(p, str) and p]

    def blocked(self, rel: str) -> bool:
        parts = rel.strip("/").split("/")
        for i in range(1, len(parts) + 1):
            sub = "/".join(parts[:i])
            for p in self.patterns:
                if fnmatch.fnmatchcase(sub, p) or fnmatch.fnmatchcase(sub + "/", p):
                    return True
        return False


# ---------------------------------------------------------------- sections

def skill_version() -> Tuple[Optional[str], Optional[str]]:
    path = Path(__file__).resolve().parent.parent / "SKILL.md"
    try:
        m = SKILL_VERSION_RE.search(path.read_text(encoding="utf-8"))
    except Exception as e:
        return None, "cannot read %s: %s" % (path, _err(e))
    if not m:
        return None, "%s has no 'Skill version: X.Y.Z' line" % path
    return m.group(1), None


def read_policy(root: Path) -> Tuple[Dict[str, Any], Optional[Blocker]]:
    out = {"ok": False, "policy_version": None, "access": None, "skill_min_version": None,
           "do_not_read_count": None, "error": None}  # type: Dict[str, Any]
    try:
        raw = json.loads((root / "science" / "policy.json").read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("policy.json is not a JSON object")
        dnr = raw.get("do_not_read", [])
        if not isinstance(dnr, list):
            raise ValueError("do_not_read must be a list")
        out.update(ok=True, policy_version=raw.get("policy_version"), access=raw.get("access", "science-only"),
                   skill_min_version=raw.get("skill_min_version", "0.0.0"), do_not_read_count=len(dnr))
        ws = raw.get("workstreams")
        if isinstance(ws, dict) and ws.get("enabled", True) is True and isinstance(ws.get("default"), str):
            queue = ws.get("queue", WS_QUEUE_DEFAULT)
            out["workstreams"] = {"default": ws["default"],
                                  "queue": queue if isinstance(queue, str) and "{ws}" in queue else WS_QUEUE_DEFAULT}
        return out, Blocker([str(p) for p in dnr])
    except Exception as e:
        out["error"] = _err(e)
        return out, None


def _git(root: Path, *args: str) -> bytes:
    proc = subprocess.run(["git", "-C", str(root)] + list(args), stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=GIT_TIMEOUT)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:]
        raise RuntimeError("git %s exited %d%s" % (args[0], proc.returncode, (": " + tail[0]) if tail else ""))
    return proc.stdout


def _git_dir(root: Path) -> Path:
    g = root / ".git"
    if g.is_dir():
        return g
    if g.is_file():  # worktree or submodule: "gitdir: <path>"
        text = g.read_text(encoding="utf-8").strip()
        if text.startswith("gitdir:"):
            p = Path(text[len("gitdir:"):].strip())
            return p if p.is_absolute() else (root / p).resolve()
    raise RuntimeError("%s/.git not found (is the repo root granted?)" % root)


def _git_from_files(root: Path) -> Tuple[Optional[str], int]:
    """HEAD commit and index entry count read straight from .git (used when the git CLI fails)."""
    gd = _git_dir(root)
    common = gd
    if (gd / "commondir").is_file():
        common = (gd / (gd / "commondir").read_text(encoding="utf-8").strip()).resolve()
    head = (gd / "HEAD").read_text(encoding="utf-8").strip()
    sha = None  # type: Optional[str]
    if head.startswith("ref:"):
        ref = head[len("ref:"):].strip()
        for base in (gd, common):
            f = base / ref
            if f.is_file():
                sha = f.read_text(encoding="utf-8").strip()
                break
        if sha is None and (common / "packed-refs").is_file():
            for line in (common / "packed-refs").read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref and not line.startswith(("#", "^")):
                    sha = parts[0]
                    break
    else:
        sha = head
    with (gd / "index").open("rb") as f:
        header = f.read(12)
    if len(header) < 12 or header[:4] != b"DIRC":
        raise RuntimeError(".git/index has an unknown format")
    return sha, int.from_bytes(header[8:12], "big")


def repo_info(root: Path, blocker: Optional[Blocker]) -> Dict[str, Any]:
    info = {"git_head": None, "tracked_files": None, "probe": None, "git": None, "git_error": None,
            "error": None}  # type: Dict[str, Any]
    if blocker is None:
        info["error"] = "skipped: science/policy.json could not be read, so do_not_read is unknown"
        return info
    errors = []  # type: List[str]
    tracked = []  # type: List[str]
    if blocker.blocked(".git/HEAD"):
        info["git_error"] = ".git matches do_not_read; git checks skipped"
        errors.append(info["git_error"])
    else:
        try:
            info["git_head"] = _git(root, "rev-parse", "HEAD").decode("utf-8", "replace").strip()
            listing = _git(root, "ls-files", "-z").decode("utf-8", "replace")
            tracked = [t for t in listing.split("\0") if t]
            info["tracked_files"] = len(tracked)
            info["git"] = "cli"
        except Exception as e:
            cli_error = _err(e)
            info["git_head"] = None
            try:
                info["git_head"], info["tracked_files"] = _git_from_files(root)
                info["git"] = "files (git CLI failed: %s)" % cli_error
            except Exception as e2:
                info["git_error"] = "git failed (%s) and .git could not be read (%s)" % (cli_error, _err(e2))
                errors.append(info["git_error"])
    candidates = [PROBE_REL] + [t for t in tracked if t != PROBE_REL and not t.startswith("science/")]
    for rel in candidates:
        if blocker.blocked(rel):
            continue
        p = root / rel
        try:
            if not p.is_file():
                continue
            info["probe"] = {"path": rel, "sha256": _sha256(p)}
        except Exception as e:
            errors.append("cannot read %s: %s" % (rel, _err(e)))
        break
    if info["probe"] is None and not errors:
        errors.append("no readable probe file: %s is missing and no tracked file outside science/ could be read"
                      % PROBE_REL)
    info["error"] = "; ".join(errors) or None
    return info


def probe_write(path: Path) -> Tuple[str, Optional[str]]:
    """Try to create (then delete) a file. Returns ("allowed"|"denied"|"error: ...", cleanup_error)."""
    for attempt in (1, 2):
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:  # left over from an earlier run
            if attempt == 2:
                return "error: %s exists and cannot be replaced" % path.name, None
            try:
                os.unlink(str(path))
            except OSError as e:
                return ("denied" if e.errno in DENIED_ERRNOS else "error: " + _err(e)), None
            continue
        except OSError as e:
            return ("denied" if e.errno in DENIED_ERRNOS else "error: " + _err(e)), None
        try:
            os.write(fd, b"science-bridge self-test probe; safe to delete\n")
        except OSError:
            pass
        finally:
            os.close(fd)
        try:
            os.unlink(str(path))
        except OSError as e:
            return "allowed", "created %s but could not delete it: %s" % (path, _err(e))
        return "allowed", None
    return "error: unreachable", None


def queue_rels(root: Path, blocker: Blocker, workstreams: Optional[Dict[str, str]]) -> List[str]:
    """The queue files: one, or with a policy.json workstreams block one per directory under
    .planning/workstreams/ plus the default workstream (same rule as bridge.py)."""
    if not workstreams:
        return [QUESTIONS_REL]
    names = set([workstreams["default"]])
    base = root / WS_DIR_REL
    if not blocker.blocked(WS_DIR_REL) and base.is_dir():
        names.update(p.name for p in base.iterdir() if p.is_dir() and WS_NAME_RE.match(p.name))
    rels = [workstreams["queue"].replace("{ws}", n) for n in sorted(names)]
    # a shared queue from before workstreams were turned on still counts (its items belong to the default one)
    legacy = QUESTIONS_REL not in rels and not blocker.blocked(QUESTIONS_REL) and (root / QUESTIONS_REL).is_file()
    return rels + ([QUESTIONS_REL] if legacy else [])


def _open_in(text: str) -> List[str]:
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == REQUESTS_HEADING), None)
    if start is None:
        return []
    out = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        m = REQUEST_LINE_RE.match(line.strip())
        if m and m.group(1) == " ":
            out.append(m.group(2))
    return out


def open_requests(root: Path, blocker: Optional[Blocker],
                  workstreams: Optional[Dict[str, str]] = None) -> Tuple[List[str], Optional[str]]:
    if blocker is None:
        return [], "skipped: science/policy.json could not be read"
    out, errors = [], []  # type: List[str], List[str]
    for rel in queue_rels(root, blocker, workstreams):
        if blocker.blocked(rel):
            errors.append("skipped: %s matches do_not_read" % rel)
            continue
        try:
            out.extend(_open_in((root / rel).read_text(encoding="utf-8")))
        except FileNotFoundError:
            continue
        except Exception as e:
            errors.append("%s: %s" % (rel, _err(e)))
    return out, ("; ".join(errors) or None)


def parse_compute(specs: List[str], errors: List[str]) -> List[Dict[str, Any]]:
    out = []
    for spec in specs:
        name, sep, rest = spec.partition("=")
        result, _, detail = rest.partition(":")
        if not sep or not HOST_RE.match(name) or result not in COMPUTE_RESULTS:
            errors.append("bad --compute %r (expected NAME=ok|failed|skipped[:detail])" % spec)
            continue
        out.append({"name": name, "result": result, "detail": detail.strip() or None})
    return out


# ---------------------------------------------------------------- main

def run(root_arg: str, compute_specs: List[str], errors: List[str]) -> Dict[str, Any]:
    root = Path(root_arg).expanduser()
    try:
        root = root.resolve()
    except Exception:
        pass
    version, version_error = skill_version()
    if version_error:
        errors.append(version_error)
    policy, blocker = read_policy(root)
    repo = repo_info(root, blocker)

    root_probe, cleanup = probe_write(root / ROOT_PROBE)
    if cleanup:
        errors.append(cleanup)
    inbox_probe, cleanup = probe_write(root / "science" / "inbox" / INBOX_PROBE)
    if cleanup:
        errors.append(cleanup)
    inbox_write = "ok" if inbox_probe == "allowed" else inbox_probe

    requests, requests_error = open_requests(root, blocker, policy.get("workstreams"))
    return {
        "format": FORMAT,
        "created": _now(),
        "skill_version": version,
        "python": platform.python_version(),
        "root": str(root),
        "policy": policy,
        "repo": repo,
        "write_probe_repo_root": root_probe,
        "inbox_write": inbox_write,
        "requests_open": requests,
        "requests_error": requests_error,
        "compute": parse_compute(compute_specs, errors),
        "errors": errors,
    }


def main(argv: List[str]) -> int:
    try:
        root_arg = None  # type: Optional[str]
        computes = []  # type: List[str]
        errors = []  # type: List[str]
        i = 0
        while i < len(argv):
            a = argv[i]
            if a in ("-h", "--help"):
                print(__doc__.strip())
                return 0
            if a == "--compute":
                if i + 1 < len(argv):
                    computes.append(argv[i + 1])
                    i += 2
                    continue
                errors.append("--compute needs a value NAME=ok|failed|skipped[:detail]")
            elif a.startswith("--compute="):
                computes.append(a[len("--compute="):])
            elif root_arg is None:
                root_arg = a
            else:
                errors.append("unexpected argument %r" % a)
            i += 1
        if root_arg is None:
            print(json.dumps({"format": FORMAT, "created": _now(),
                              "errors": ["usage: python3 scripts/selftest.py <repo-root> "
                                         "[--compute NAME=ok|failed|skipped[:detail] ...]"]}, indent=2))
            return 2

        result = run(root_arg, computes, errors)
        out_path = Path(result["root"]) / "science" / "inbox" / "selftest.json"
        written = False
        try:
            out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            written = True
        except Exception as e:
            errors.append("could not write %s: %s" % (out_path, _err(e)))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if written else 1
    except Exception as e:  # never raise
        try:
            print(json.dumps({"format": FORMAT, "created": _now(), "errors": ["self-test crashed: " + _err(e)]}))
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
