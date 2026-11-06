"""Tests for science-bridge. Standard library only.

Run from the repo root:  python3 -m unittest discover -s tests -v
"""

import datetime as _dt
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_SCRIPTS = ROOT / "cs-skill" / "science-findings" / "scripts"
SELFTEST = SKILL_SCRIPTS / "selftest.py"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(SKILL_SCRIPTS))

import bridge  # noqa: E402
from check_finding import validate_finding  # noqa: E402

VALID = """---
format: science-finding/v0.1
title: Larynx stage groups extracted
type: rule-check
created: 2026-11-06
author: claude-science
confidence: medium
sources:
  - outbox/ca_larynx.docx
  - https://example.org/page#section
  - inbox/2026-11-06-larynx-groups.csv
contains_case_data: false
affects: [LBL-01, LBL-06]
---

## Summary
Extracted the larynx table.

## Details
Table attached.

## Suggested action
Review LBL-01.
"""


def run(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = bridge.main(argv)
    out = buf.getvalue()
    try:
        return code, json.loads(out) if out.strip() else None
    except ValueError:
        return code, out


V2_POLICY = {
    "policy_version": 2,
    "access": "science-only",  # its tests export repo files, which only science-only allows
    "do_not_read": ["prereg/*"],
    "privacy": "phi",
    "allowed_sources": ["docs/*.md"],
    "allowed_extensions": [".md", ".csv"],
    "check_command": None,
    "max_file_mb": 5,
    "team_note": "kept by migration",
}

HAS_GIT = shutil.which("git") is not None
IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.org",
                    "-c", "commit.gpgsign=false"] + list(args), check=True, capture_output=True, timeout=60)


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2))


def read_json(path):
    return json.loads(Path(path).read_text())


def confirm_skill(root, version=None):
    """Pretend Claude Science already passed a self-test with the bundled skill."""
    write_json(Path(root) / "science" / "archive" / "selftest-latest.json",
               {"format": bridge.SELFTEST_FORMAT, "created": bridge._now(),
                "skill_version": version or bridge.SKILL_VERSION})


class FindingValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def write(self, name, text):
        p = self.tmp / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_valid_finding(self):
        r = validate_finding(self.write("2026-11-06-larynx.md", VALID))
        self.assertTrue(r["ok"], r["errors"])
        self.assertEqual(r["meta"]["affects"], ["LBL-01", "LBL-06"])

    def test_case_data_flag_must_be_false(self):
        r = validate_finding(self.write("2026-11-06-x.md", VALID.replace("contains_case_data: false", "contains_case_data: true")))
        self.assertFalse(r["ok"])
        self.assertTrue(any("contains_case_data" in e for e in r["errors"]))

    def test_missing_key_and_bad_type(self):
        text = VALID.replace("confidence: medium\n", "").replace("type: rule-check", "type: gossip")
        r = validate_finding(self.write("2026-11-06-x.md", text))
        self.assertIn("missing required key 'confidence'", r["errors"])
        self.assertTrue(any(e.startswith("type must be") for e in r["errors"]))

    def test_bad_filename(self):
        r = validate_finding(self.write("Larynx Finding.md", VALID))
        self.assertTrue(any("filename" in e for e in r["errors"]))

    def test_heading_order_and_presence(self):
        swapped = VALID.replace("## Details\nTable attached.\n\n## Suggested action\nReview LBL-01.",
                                "## Suggested action\nReview.\n\n## Details\nTable.")
        r = validate_finding(self.write("2026-11-06-x.md", swapped))
        self.assertTrue(any("order" in e for e in r["errors"]))
        r = validate_finding(self.write("2026-11-06-y.md", VALID.replace("## Summary", "## Overview")))
        self.assertIn("missing body heading '## Summary'", r["errors"])

    def test_wrong_format_version_and_empty_sources(self):
        text = VALID.replace("science-finding/v0.1", "science-finding/v9").replace(
            "sources:\n  - outbox/ca_larynx.docx\n  - https://example.org/page#section\n  - inbox/2026-11-06-larynx-groups.csv\n",
            "sources: []\n")
        r = validate_finding(self.write("2026-11-06-x.md", text))
        self.assertTrue(any(e.startswith("format must be") for e in r["errors"]))
        self.assertIn("sources must be a non-empty list", r["errors"])

    def test_no_frontmatter(self):
        r = validate_finding(self.write("2026-11-06-x.md", "# just text\n"))
        self.assertFalse(r["ok"])


class BridgeFlow(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "docs").mkdir()
        (self.root / "docs" / "guide.md").write_text("AJCC guide text\n")
        (self.root / "data").mkdir()
        (self.root / "data" / "cases.csv").write_text("id,report\n1,sensitive report text\n")
        code, out = run(["--project-dir", str(self.root), "init"])
        self.assertEqual(code, 0, out)
        self.sci = self.root / "science"
        self.set_policy(access="science-only")  # these tests export repo files; repo-readonly refuses that

    def set_policy(self, **overrides):
        policy = json.loads((self.sci / "policy.json").read_text())
        for key, value in overrides.items():
            if key in bridge.EXPORT_KEYS:
                policy["export"][key] = value
            else:
                policy[key] = value
        (self.sci / "policy.json").write_text(json.dumps(policy))

    def test_init_is_idempotent_and_never_overwrites(self):
        readme = self.sci / "README.md"
        readme.write_text(readme.read_text().replace("## Project brief\n", "## Project brief\n\ncustom brief\n"))
        before, policy_before = readme.read_text(), (self.sci / "policy.json").read_text()
        code, out = run(["--project-dir", str(self.root), "init"])
        self.assertEqual(code, 0)
        self.assertEqual(out["created"], [])
        self.assertEqual((out["policy"], out["readme"]), ("unchanged", "unchanged"))
        self.assertEqual(readme.read_text(), before)
        self.assertIn("custom brief", before)
        self.assertEqual((self.sci / "policy.json").read_text(), policy_before)
        for sub in ("outbox", "inbox", "archive", "runs"):
            self.assertTrue((self.sci / sub).is_dir())
        self.assertTrue((self.sci / "runs" / ".gitkeep").is_file())

    def test_phi_requires_whitelist(self):
        self.set_policy(privacy="phi")
        code, out = run(["--project-dir", str(self.root), "export", str(self.root / "docs" / "guide.md")])
        self.assertEqual(code, 1)
        self.assertIn("allowed_sources", out["error"])

    def test_export_whitelisted_and_all_or_nothing(self):
        self.set_policy(allowed_sources=["docs/*.md"])
        code, out = run(["--project-dir", str(self.root), "export",
                         str(self.root / "docs" / "guide.md"), str(self.root / "data" / "cases.csv")])
        self.assertEqual(code, 1)
        self.assertFalse((self.sci / "outbox" / "guide.md").exists(), "all-or-nothing violated")
        self.assertTrue(any("cases.csv" in e for e in out["errors"]))

        code, out = run(["--project-dir", str(self.root), "export", str(self.root / "docs" / "guide.md")])
        self.assertEqual(code, 0, out)
        manifest = json.loads((self.sci / "outbox" / "MANIFEST.json").read_text())
        self.assertEqual([f["name"] for f in manifest["files"]], ["guide.md"])
        self.assertEqual(manifest["files"][0]["source"], "docs/guide.md")

        code, out = run(["--project-dir", str(self.root), "export", str(self.root / "docs" / "guide.md")])
        self.assertEqual(out["exported"][0]["status"], "unchanged")

        (self.root / "docs" / "guide.md").write_text("AJCC guide text, revised\n")
        code, out = run(["--project-dir", str(self.root), "check"])
        self.assertEqual(code, 0, "a stale copy is a warning, not a violation")
        self.assertIn("docs/guide.md changed since export", " ".join(out["outbox"]["stale"]))
        code, out = run(["--project-dir", str(self.root), "export", str(self.root / "docs" / "guide.md")])
        self.assertEqual(out["exported"][0]["status"], "updated")
        code, out = run(["--project-dir", str(self.root), "check"])
        self.assertEqual(out["outbox"]["stale"], [])

    def test_export_refuses_outside_project_and_symlink_escape(self):
        self.set_policy(privacy="public")
        outside = Path(tempfile.mkdtemp()) / "secret.md"
        outside.write_text("x")
        link = self.root / "docs" / "link.md"
        os.symlink(str(outside), str(link))
        code, out = run(["--project-dir", str(self.root), "export", str(outside)])
        self.assertEqual(code, 1)
        code, out = run(["--project-dir", str(self.root), "export", str(link)])
        self.assertEqual(code, 1)
        self.assertTrue(any("outside" in e for e in out["errors"]))

    def test_check_command_gate(self):
        gate = self.root / "gate.py"
        gate.write_text("import sys\nsys.exit(1 if 'report' in open(sys.argv[1]).read() else 0)\n")
        self.set_policy(privacy="confidential", allowed_sources=[],
                        check_command="%s %s {file}" % (sys.executable, gate))
        code, out = run(["--project-dir", str(self.root), "export", str(self.root / "data" / "cases.csv")])
        self.assertEqual(code, 1)
        self.assertTrue(any("check_command refused" in e for e in out["errors"]))
        code, out = run(["--project-dir", str(self.root), "export", str(self.root / "docs" / "guide.md")])
        self.assertEqual(code, 0, out)

    def test_check_detects_hand_copied_and_tampered_files(self):
        self.set_policy(allowed_sources=["docs/*.md"])
        run(["--project-dir", str(self.root), "export", str(self.root / "docs" / "guide.md")])
        code, out = run(["--project-dir", str(self.root), "check"])
        self.assertEqual(code, 0, out)
        (self.sci / "outbox" / "cases.csv").write_text("sensitive")
        (self.sci / "outbox" / "guide.md").write_text("tampered")
        code, out = run(["--project-dir", str(self.root), "check"])
        self.assertEqual(code, 1)
        joined = " ".join(out["outbox"]["violations"])
        self.assertIn("cases.csv: not in MANIFEST.json", joined)
        self.assertIn("guide.md: content differs", joined)

    def test_archive_moves_finding_and_attachment_and_logs(self):
        (self.sci / "inbox" / "2026-11-06-larynx.md").write_text(VALID)
        (self.sci / "inbox" / "2026-11-06-larynx-groups.csv").write_text("a,b\n")
        code, out = run(["--project-dir", str(self.root), "status"])
        self.assertTrue(out["pending"][0]["ok"])
        self.assertEqual(out["attachments"], ["2026-11-06-larynx-groups.csv"])
        code, out = run(["--project-dir", str(self.root), "archive", "2026-11-06-larynx.md",
                         "--route", "note", "--ref", ".planning/notes/x.md"])
        self.assertEqual(code, 0, out)
        self.assertTrue((self.sci / "archive" / "2026-11-06-larynx.md").exists())
        self.assertTrue((self.sci / "archive" / "2026-11-06-larynx-groups.csv").exists())
        self.assertFalse((self.sci / "inbox" / "2026-11-06-larynx.md").exists())
        log = (self.sci / "archive" / "routing.jsonl").read_text().strip().splitlines()
        self.assertEqual(json.loads(log[-1])["route"], "note")

    def test_archive_refuses_invalid_unless_rejected(self):
        (self.sci / "inbox" / "2026-11-06-bad.md").write_text("no frontmatter\n")
        code, out = run(["--project-dir", str(self.root), "archive", "2026-11-06-bad.md",
                         "--route", "note", "--ref", "x"])
        self.assertEqual(code, 1)
        code, out = run(["--project-dir", str(self.root), "archive", "2026-11-06-bad.md",
                         "--route", "rejected", "--ref", "invalid format"])
        self.assertEqual(code, 0, out)


class GsdIntegration(unittest.TestCase):
    def setUp(self):
        self._which = bridge.shutil.which
        bridge.shutil.which = lambda name: None  # don't call the real gsd-sdk from unit tests
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".planning").mkdir()
        self.cfg_path = self.root / ".planning" / "config.json"
        self.cfg_path.write_text(json.dumps({
            "mode": "yolo",
            "agent_skills": {"gsd-planner": [".claude/skills/house-style"], "gsd-executor": "global:lint"},
            "workflow": {"research": True},
        }, indent=2))

    def tearDown(self):
        bridge.shutil.which = self._which

    def cfg(self):
        return json.loads(self.cfg_path.read_text())

    def test_gsd_setup_requires_gsd_project(self):
        plain = Path(tempfile.mkdtemp())
        run(["--project-dir", str(plain), "init"])
        code, out = run(["--project-dir", str(plain), "gsd-setup"])
        self.assertEqual(code, 1)
        self.assertIn(".planning/config.json", out["error"])

    def test_init_runs_gsd_setup_and_merges_without_clobbering(self):
        code, out = run(["--project-dir", str(self.root), "init"])
        self.assertEqual(code, 0, out)
        self.assertIn("installed", out["gsd"]["skill"])
        skill = self.root / ".claude" / "skills" / "science-context" / "SKILL.md"
        self.assertEqual(skill.read_bytes(), bridge.GSD_SKILL_TEMPLATE.read_bytes())
        cfg = self.cfg()
        self.assertEqual(cfg["mode"], "yolo")
        self.assertEqual(cfg["workflow"], {"research": True})
        self.assertEqual(cfg["agent_skills"]["gsd-planner"], [".claude/skills/house-style", ".claude/skills/science-context"])
        self.assertEqual(cfg["agent_skills"]["gsd-executor"], "global:lint", "agents not in the list must be untouched")
        for agent in bridge.GSD_DEFAULT_AGENTS:
            self.assertIn(".claude/skills/science-context", cfg["agent_skills"][agent])

        code, out = run(["--project-dir", str(self.root), "gsd-setup"])
        self.assertEqual(out["agents_added"], [])
        self.assertIn("unchanged", out["skill"])
        self.assertEqual(self.cfg()["agent_skills"]["gsd-planner"].count(".claude/skills/science-context"), 1)

    def test_no_gsd_flag_and_custom_agents(self):
        code, out = run(["--project-dir", str(self.root), "init", "--no-gsd"])
        self.assertTrue(str(out["gsd"]).startswith("skipped"))
        self.assertNotIn("gsd-phase-researcher", self.cfg()["agent_skills"])
        code, out = run(["--project-dir", str(self.root), "gsd-setup", "--agents", "gsd-verifier"])
        self.assertEqual(out["agents_added"], ["gsd-verifier"])
        code, out = run(["--project-dir", str(self.root), "gsd-setup", "--agents", "bad agent;rm"])
        self.assertEqual(code, 1)

    def test_outdated_skill_is_reported_and_refreshed(self):
        run(["--project-dir", str(self.root), "init"])
        skill = self.root / ".claude" / "skills" / "science-context" / "SKILL.md"
        skill.write_text("old version")
        code, out = run(["--project-dir", str(self.root), "status"])
        self.assertEqual(out["gsd"]["skill"], "outdated")
        code, out = run(["--project-dir", str(self.root), "gsd-setup"])
        self.assertIn("updated", out["skill"])
        code, out = run(["--project-dir", str(self.root), "check"])
        self.assertEqual(out["gsd"]["skill"], "current")
        self.assertEqual(out["gsd"]["agents_missing"], [])


class RequestQueueAndAccess(unittest.TestCase):
    def setUp(self):
        self._which = bridge.shutil.which
        bridge.shutil.which = lambda name: None
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".planning" / "research").mkdir(parents=True)
        (self.root / ".planning" / "config.json").write_text("{}")
        (self.root / ".planning" / "research" / "questions.md").write_text("# Open Research Questions\n\n## Other\n\n- x\n")
        run(["--project-dir", str(self.root), "init"])
        self.q = self.root / ".planning" / "research" / "questions.md"

    def tearDown(self):
        bridge.shutil.which = self._which

    def test_template_defaults_to_repo_readonly(self):
        code, out = run(["--project-dir", str(self.root), "status"])
        self.assertEqual(out["access"], "repo-readonly")
        policy = json.loads((self.root / "science" / "policy.json").read_text())
        policy["access"] = "everything"
        (self.root / "science" / "policy.json").write_text(json.dumps(policy))
        code, out = run(["--project-dir", str(self.root), "status"])
        self.assertEqual(code, 1)
        self.assertIn("access must be one of", out["error"])

    def test_gsd_setup_adds_section_once_and_keeps_other_content(self):
        text = self.q.read_text()
        self.assertEqual(text.count(bridge.REQUESTS_HEADING), 1)
        self.assertIn("## Other", text)
        run(["--project-dir", str(self.root), "gsd-setup"])
        self.assertEqual(self.q.read_text().count(bridge.REQUESTS_HEADING), 1)

    def test_ask_numbers_dedupes_and_archive_ticks(self):
        code, out = run(["--project-dir", str(self.root), "ask", "Check oral cavity tables",
                         "--by", "gsd-planner phase 3", "--context", "docs/oral.docx", "--affects", "LBL-01"])
        self.assertEqual(out["id"], "SQ-001")
        code, out = run(["--project-dir", str(self.root), "ask", "Literature review"])
        self.assertEqual(out["id"], "SQ-002")
        code, out = run(["--project-dir", str(self.root), "ask", "check oral cavity  tables"])
        self.assertEqual(code, 1)
        self.assertIn("SQ-001", out["error"])
        code, out = run(["--project-dir", str(self.root), "requests"])
        self.assertEqual([r["id"] for r in out["items"]], ["SQ-001", "SQ-002"])
        text = self.q.read_text()
        self.assertLess(text.index(bridge.REQUESTS_HEADING), text.index("SQ-001"), "items go inside their section")
        self.assertIn("## Other\n\n- x\n", text, "other sections are untouched")

        finding = VALID.replace("affects: [LBL-01, LBL-06]", "affects: [LBL-01]\nanswers: [SQ-001, SQ-099]")
        (self.root / "science" / "inbox" / "2026-11-06-oral.md").write_text(finding)
        code, out = run(["--project-dir", str(self.root), "archive", "2026-11-06-oral.md", "--route", "note", "--ref", "n"])
        self.assertEqual(code, 0, out)
        self.assertEqual(out["requests"], {"ticked": ["SQ-001"], "already": [], "unknown": ["SQ-099"]})
        self.assertIn("- [x] SQ-001", self.q.read_text())
        self.assertIn("answered: science/archive/2026-11-06-oral.md", self.q.read_text())
        code, out = run(["--project-dir", str(self.root), "requests"])
        self.assertEqual([r["id"] for r in out["items"]], ["SQ-002"])

    def test_validator_checks_answers_and_builds_on(self):
        p = self.root / "2026-11-06-x.md"
        p.write_text(VALID.replace("affects: [LBL-01, LBL-06]", "answers: [Q1]\nbuilds_on: notes.md"))
        r = validate_finding(p)
        self.assertTrue(any("invalid request IDs" in e for e in r["errors"]))
        self.assertIn("builds_on must be a list of repo paths or archived findings", r["errors"])

    def test_export_refuses_do_not_read(self):
        (self.root / "prereg").mkdir()
        (self.root / "prereg" / "sealed.md").write_text("fold ids")
        policy = json.loads((self.root / "science" / "policy.json").read_text())
        policy.update(privacy="public", do_not_read=["prereg/sealed*"], access="science-only")
        (self.root / "science" / "policy.json").write_text(json.dumps(policy))
        code, out = run(["--project-dir", str(self.root), "export", str(self.root / "prereg" / "sealed.md")])
        self.assertEqual(code, 1)
        self.assertTrue(any("do_not_read" in e for e in out["errors"]))
        os.symlink(str(self.root / "prereg" / "sealed.md"), str(self.root / "alias.md"))
        code, out = run(["--project-dir", str(self.root), "export", str(self.root / "alias.md")])
        self.assertEqual(code, 1, "a symlink to a do_not_read file is refused too")
        self.assertTrue(any("do_not_read" in e for e in out["errors"]))


class OutboxModes(unittest.TestCase):
    """Outside-repo exports, the repo-readonly refusal, redundant old copies and outbox-clean."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        (self.root / "docs").mkdir()
        (self.root / "docs" / "guide.md").write_text("guide\n")
        (self.root / "docs" / "notes.md").write_text("notes\n")
        self.ext_named = Path(tempfile.mkdtemp())  # as named; /var/... -> /private/var/... on macOS
        self.ext = self.ext_named.resolve()
        (self.ext / "dictionary.csv").write_text("col,meaning\n")
        (self.ext / "other.txt").write_text("x\n")
        secret_dir = Path(tempfile.mkdtemp()).resolve()
        (secret_dir / "secret.csv").write_text("not allowed\n")
        os.symlink(str(secret_dir / "secret.csv"), str(self.ext / "link.csv"))
        code, out = run(["--project-dir", str(self.root), "init"])
        self.assertEqual(code, 0, out)
        self.sci = self.root / "science"
        self.policy_path = self.sci / "policy.json"

    def set_policy(self, **overrides):
        policy = read_json(self.policy_path)
        for key, value in overrides.items():
            (policy["export"] if key in bridge.EXPORT_KEYS else policy)[key] = value
        write_json(self.policy_path, policy)

    def cli(self, *argv):
        return run(["--project-dir", str(self.root)] + [str(a) for a in argv])

    def manifest_names(self):
        return [f["name"] for f in read_json(self.sci / "outbox" / "MANIFEST.json")["files"]]

    def test_outside_export_needs_an_absolute_pattern(self):
        for patterns in ([], ["docs/*.md", "*.csv"]):
            self.set_policy(allowed_sources=patterns)
            code, out = self.cli("export", self.ext / "dictionary.csv")
            self.assertEqual(code, 1, patterns)
            self.assertIn("matches no absolute export.allowed_sources pattern", out["errors"][0])
        self.assertEqual(self.manifest_names(), [])

        self.set_policy(allowed_sources=[str(self.ext_named) + "/*.csv"])
        code, out = self.cli("export", self.ext_named / "dictionary.csv")
        self.assertEqual(code, 0, out)
        real = str(self.ext / "dictionary.csv")
        self.assertEqual(out["exported"], [{"name": "dictionary.csv", "source": real, "status": "added"}])
        entry = read_json(self.sci / "outbox" / "MANIFEST.json")["files"][0]
        self.assertEqual(entry["source"], real)
        self.assertTrue((self.sci / "outbox" / "dictionary.csv").is_file())
        code, out = self.cli("export", self.ext / "dictionary.csv")
        self.assertEqual(out["exported"][0]["status"], "unchanged")

        code, out = self.cli("export", self.ext / "other.txt")
        self.assertEqual(code, 1, "an outside file that matches no absolute pattern is refused")
        self.assertIn("matches no absolute", out["errors"][0])
        code, out = self.cli("export", self.ext / "link.csv")
        self.assertEqual(code, 1, "patterns match the real path, so a symlink can't smuggle a file in")
        self.assertIn("secret.csv", out["errors"][0])
        self.set_policy(allowed_sources=[str(self.ext) + "/*"], allowed_extensions=[".csv"])
        code, out = self.cli("export", self.ext / "other.txt")
        self.assertEqual(code, 1, "other checks still apply outside the repo")
        self.assertIn("allowed_extensions", out["errors"][0])

        (self.ext / "dictionary.csv").write_text("col,meaning,changed\n")
        code, out = self.cli("check")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["outbox"]["stale"],
                         ["outbox/dictionary.csv: %s changed since export; re-run export to refresh" % real])
        self.assertEqual(out["outbox"]["redundant"], [])

    def test_home_relative_pattern(self):
        saved = os.environ.get("HOME")
        os.environ["HOME"] = str(self.ext.parent)
        try:
            self.set_policy(allowed_sources=["~/%s/*.csv" % self.ext.name])
            code, out = self.cli("export", "~/%s/dictionary.csv" % self.ext.name)
        finally:
            if saved is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = saved
        self.assertEqual(code, 0, out)
        self.assertEqual(out["exported"][0]["source"], str(self.ext / "dictionary.csv"))

    def test_repo_readonly_refuses_repo_files(self):
        self.set_policy(privacy="public")
        for path in (self.root / "docs" / "guide.md", "docs/guide.md"):
            code, out = self.cli("export", path)
            self.assertEqual(code, 1, path)
            self.assertEqual(out["errors"], ["docs/guide.md: Claude Science reads repo files directly in "
                                             "repo-readonly mode; export is only for files outside the repo"])
        self.assertEqual(self.manifest_names(), [])
        self.set_policy(access="science-only")
        code, out = self.cli("export", "docs/guide.md")
        self.assertEqual(code, 0, "science-only still exports repo files")
        self.assertEqual(out["exported"][0]["source"], "docs/guide.md")

    def test_redundant_copies_and_outbox_clean(self):
        self.set_policy(access="science-only", privacy="public")
        code, out = self.cli("export", "docs/guide.md", "docs/notes.md")  # made before the switch
        self.assertEqual(code, 0, out)
        self.set_policy(access="repo-readonly", allowed_sources=[str(self.ext) + "/*.csv"])
        code, out = self.cli("export", self.ext / "dictionary.csv")
        self.assertEqual(code, 0, out)
        (self.root / "docs" / "guide.md").write_text("guide, edited\n")
        old = [{"name": "guide.md", "source": "docs/guide.md"}, {"name": "notes.md", "source": "docs/notes.md"}]

        code, out = self.cli("check")
        self.assertEqual(code, 0, "redundant copies are not a problem")
        self.assertEqual((out["outbox"]["redundant"], out["outbox"]["stale"]), (old, []))
        code, out = self.cli("status")
        self.assertEqual(out["outbox"]["redundant"], old)
        code, out = self.cli("init")
        self.assertEqual(out["outbox_redundant"], old)
        self.assertIn("outbox-clean", out["next_steps"][-1])

        code, out = self.cli("outbox-clean")
        self.assertEqual(code, 0, out)
        self.assertEqual((out["ok"], out["would_remove"]), (True, old))
        self.assertEqual(self.manifest_names(), ["dictionary.csv", "guide.md", "notes.md"], "dry run changes nothing")
        self.assertTrue((self.sci / "outbox" / "guide.md").is_file())

        (self.sci / "outbox" / "notes.md").unlink()
        code, out = self.cli("check")
        self.assertEqual(code, 1)
        self.assertIn("outbox/notes.md: listed in MANIFEST.json but missing", out["outbox"]["violations"])
        code, out = self.cli("outbox-clean", "--yes")
        self.assertEqual(code, 0, out)
        self.assertEqual((out["removed"], out["kept"]), (old, ["dictionary.csv"]))
        self.assertEqual(self.manifest_names(), ["dictionary.csv"])
        self.assertEqual(sorted(p.name for p in (self.sci / "outbox").iterdir()), ["MANIFEST.json", "dictionary.csv"])
        code, out = self.cli("check")
        self.assertEqual(code, 0, out)
        self.assertEqual((out["outbox"]["violations"], out["outbox"]["redundant"]), ([], []))
        code, out = self.cli("outbox-clean")
        self.assertEqual(out["would_remove"], [])
        code, out = self.cli("init")
        self.assertEqual(out["outbox_redundant"], [])

    def test_outbox_clean_needs_repo_readonly(self):
        self.set_policy(access="science-only")
        code, out = self.cli("outbox-clean", "--yes")
        self.assertEqual(code, 1)
        self.assertIn('only applies to access "repo-readonly"', out["error"])


class SessionHint(unittest.TestCase):
    def hint(self, cwd, plugins_dir=None):
        env = {**os.environ, "CLAUDE_PROJECT_DIR": "",
               "SCIENCE_BRIDGE_PLUGINS_DIR": str(plugins_dir or tempfile.mkdtemp())}
        proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "bridge.py"), "session-hint"],
                              input=json.dumps({"cwd": str(cwd), "hook_event_name": "SessionStart"}),
                              capture_output=True, text=True, timeout=30, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        return proc.stdout

    def fake_marketplace(self, version, name="test-mkt"):
        """A plugins dir whose known marketplace offers science-bridge VERSION."""
        plugins, mkt = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        (mkt / ".claude-plugin").mkdir()
        write_json(mkt / ".claude-plugin" / "marketplace.json",
                   {"name": name, "plugins": [{"name": "other", "source": "./other"},
                                              {"name": "science-bridge", "source": "./"}]})
        write_json(mkt / ".claude-plugin" / "plugin.json", {"name": "science-bridge", "version": version})
        write_json(plugins / "known_marketplaces.json",
                   {"broken": {"installLocation": "/nonexistent/x"}, "weird": 42,
                    name: {"source": {"source": "directory", "path": str(mkt)}, "installLocation": str(mkt)}})
        return plugins

    def test_silent_without_science_folder(self):
        self.assertEqual(self.hint(tempfile.mkdtemp()), "")

    def test_reports_pending_and_outbox_problems_from_subdir(self):
        root = Path(tempfile.mkdtemp())
        run(["--project-dir", str(root), "init"])
        confirm_skill(root)
        self.assertEqual(self.hint(root), "")
        (root / "science" / "inbox" / "2026-11-06-larynx.md").write_text(VALID)
        (root / "science" / "outbox" / "stray.csv").write_text("x")
        (root / "src").mkdir()
        out = self.hint(root / "src")
        self.assertIn("1 Claude Science finding(s) waiting", out)
        self.assertIn("WARNING: science/outbox", out)

    def test_redundant_outbox_hint(self):
        root = Path(tempfile.mkdtemp())
        (root / "docs").mkdir()
        (root / "docs" / "guide.md").write_text("guide\n")
        run(["--project-dir", str(root), "init"])
        confirm_skill(root)
        policy_path = root / "science" / "policy.json"
        policy = read_json(policy_path)
        write_json(policy_path, {**policy, "access": "science-only"})
        code, out = run(["--project-dir", str(root), "export", "docs/guide.md"])
        self.assertEqual(code, 0, out)
        self.assertEqual(self.hint(root), "", "under science-only the copy is not redundant")
        write_json(policy_path, policy)
        self.assertEqual(self.hint(root).strip(),
                         "[science-bridge] science/outbox has 1 old copies of repo files; Claude Science now reads the "
                         "repo directly. Run /science-bridge:bridge outbox-clean to remove them.")
        run(["--project-dir", str(root), "outbox-clean", "--yes"])
        self.assertEqual(self.hint(root), "")

    def test_reports_missing_gsd_skill_in_gsd_project(self):
        root = Path(tempfile.mkdtemp())
        (root / ".planning").mkdir()
        (root / ".planning" / "config.json").write_text("{}")
        run(["--project-dir", str(root), "init", "--no-gsd"])
        self.assertIn("science-context skill is missing", self.hint(root))

    def test_plugin_update_hint_uses_known_marketplaces(self):
        root = Path(tempfile.mkdtemp())
        run(["--project-dir", str(root), "init"])
        confirm_skill(root)
        out = self.hint(root, self.fake_marketplace("9.9.9"))
        self.assertIn("[science-bridge] v9.9.9 is available (this session runs v%s)" % bridge.PLUGIN_VERSION, out)
        self.assertIn("claude plugin marketplace update test-mkt && claude plugin update science-bridge@test-mkt", out)
        self.assertIn("restart Claude Code", out)
        self.assertEqual(self.hint(root, self.fake_marketplace(bridge.PLUGIN_VERSION)), "")
        self.assertEqual(self.hint(root, self.fake_marketplace("0.0.1")), "")

    def test_legacy_policy_selftest_and_unconfirmed_skill_hints(self):
        root = Path(tempfile.mkdtemp())
        run(["--project-dir", str(root), "init"])
        out = self.hint(root)
        self.assertIn("hasn't confirmed skill v%s yet" % bridge.SKILL_VERSION, out)
        confirm_skill(root, "0.3.0")
        self.assertIn("hasn't confirmed skill v%s yet" % bridge.SKILL_VERSION, self.hint(root))
        confirm_skill(root)
        self.assertEqual(self.hint(root), "")
        write_json(root / "science" / "inbox" / "selftest.json", {"format": bridge.SELFTEST_FORMAT})
        out = self.hint(root)
        self.assertIn("run /science-bridge:bridge verify", out)
        self.assertNotIn("finding(s) waiting", out, "selftest.json is not a finding")
        write_json(root / "science" / "policy.json", V2_POLICY)
        self.assertIn("run /science-bridge:bridge init to upgrade science/ (policy v3)", self.hint(root))

    def test_at_most_six_lines_and_broken_policy_is_silent(self):
        root = Path(tempfile.mkdtemp())
        (root / ".planning" / "research").mkdir(parents=True)
        (root / ".planning" / "config.json").write_text("{}")
        (root / ".planning" / "research" / "questions.md").write_text(
            "# Q\n\n## For Claude Science\n\n- [ ] SQ-001 (2026-11-06, user): open\n")
        run(["--project-dir", str(root), "init", "--no-gsd"])
        write_json(root / "science" / "policy.json", V2_POLICY)
        (root / "science" / "outbox" / "stray.csv").write_text("x")
        (root / "science" / "inbox" / "2026-11-06-larynx.md").write_text(VALID)
        write_json(root / "science" / "inbox" / "selftest.json", {})
        lines = self.hint(root, self.fake_marketplace("9.9.9")).strip().splitlines()
        self.assertEqual(len(lines), 6)
        self.assertIn("WARNING", lines[0])
        (root / "science" / "policy.json").write_text("{not json")
        out = self.hint(root)
        self.assertNotIn("Traceback", out)


class Versions(unittest.TestCase):
    def test_versions_are_consistent(self):
        plugin = read_json(ROOT / ".claude-plugin" / "plugin.json")
        market = read_json(ROOT / ".claude-plugin" / "marketplace.json")
        entry = next(p for p in market["plugins"] if p["name"] == "science-bridge")
        self.assertEqual(plugin["version"], bridge.PLUGIN_VERSION)
        self.assertEqual(entry["version"], bridge.PLUGIN_VERSION)
        self.assertRegex(bridge.SKILL_VERSION, r"^\d+\.\d+\.\d+$")
        self.assertNotEqual(bridge.SKILL_VERSION, "0.0.0", "SKILL.md needs a 'Skill version: X.Y.Z' line")
        template = read_json(ROOT / "templates" / "policy.json")
        self.assertEqual(template["policy_version"], bridge.POLICY_VERSION)
        self.assertLessEqual(bridge.vtuple(template["skill_min_version"]), bridge.vtuple(bridge.SKILL_VERSION))

    def test_vtuple(self):
        self.assertGreater(bridge.vtuple("0.10.0"), bridge.vtuple("0.9.9"))
        self.assertEqual(bridge.vtuple("0.4"), (0, 4, 0))
        self.assertEqual(bridge.vtuple(None), (0, 0, 0))
        self.assertEqual(bridge.vtuple("v1.2.3-beta"), (1, 2, 3))


class PolicyMigration(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "docs").mkdir()
        (self.root / "docs" / "guide.md").write_text("guide\n")
        (self.root / "data").mkdir()
        (self.root / "data" / "cases.csv").write_text("id\n1\n")
        run(["--project-dir", str(self.root), "init"])
        self.policy_path = self.root / "science" / "policy.json"

    def test_v2_works_in_memory_then_init_migrates_on_disk_keeping_values(self):
        write_json(self.policy_path, V2_POLICY)
        code, out = run(["--project-dir", str(self.root), "status"])
        self.assertEqual(code, 0, out)
        self.assertTrue(out["legacy_policy"])
        self.assertEqual(out["policy_version"], 2)
        code, out = run(["--project-dir", str(self.root), "export", "docs/guide.md", "data/cases.csv"])
        self.assertEqual(code, 1, "legacy top-level allowed_sources must still be enforced")
        self.assertTrue(any("cases.csv" in e and "allowed_sources" in e for e in out["errors"]))
        code, out = run(["--project-dir", str(self.root), "export", "docs/guide.md"])
        self.assertEqual(code, 0, out)

        code, out = run(["--project-dir", str(self.root), "init"])
        self.assertEqual(code, 0, out)
        self.assertEqual(out["policy"], "migrated v2→v3")
        disk = read_json(self.policy_path)
        self.assertEqual(disk["policy_version"], 3)
        self.assertEqual(disk["export"], {k: V2_POLICY[k] for k in bridge.EXPORT_KEYS})
        for key in bridge.EXPORT_KEYS:
            self.assertNotIn(key, disk)
        for key in ("access", "do_not_read", "privacy", "team_note"):
            self.assertEqual(disk[key], V2_POLICY[key])
        self.assertEqual(disk["compute"], {})
        self.assertEqual(disk["skill_min_version"], bridge.SKILL_VERSION)
        self.assertEqual(list(disk)[:7], ["policy_version", "access", "do_not_read", "privacy", "skill_min_version",
                                          "compute", "export"])

        code, out = run(["--project-dir", str(self.root), "init"])
        self.assertEqual(out["policy"], "unchanged")
        code, out = run(["--project-dir", str(self.root), "status"])
        self.assertFalse(out["legacy_policy"])
        code, out = run(["--project-dir", str(self.root), "export", "data/cases.csv"])
        self.assertEqual(code, 1, "migrated whitelist still enforced")

    def test_v1_without_access_defaults_to_science_only(self):
        write_json(self.policy_path, {"privacy": "confidential", "allowed_sources": [], "max_file_mb": 10})
        code, out = run(["--project-dir", str(self.root), "status"])
        self.assertEqual((out["access"], out["legacy_policy"]), ("science-only", True))
        code, out = run(["--project-dir", str(self.root), "init"])
        self.assertEqual(out["policy"], "migrated v1→v3")
        disk = read_json(self.policy_path)
        self.assertEqual(disk["access"], "science-only")
        self.assertEqual(disk["do_not_read"], [])
        self.assertEqual(disk["export"], {"allowed_sources": [], "allowed_extensions": [], "check_command": None,
                                          "max_file_mb": 10})

    def test_skill_min_version_is_raised_never_lowered(self):
        policy = read_json(self.policy_path)
        policy["skill_min_version"] = "0.1.0"
        write_json(self.policy_path, policy)
        code, out = run(["--project-dir", str(self.root), "init"])
        self.assertEqual(out["policy"], "updated")
        self.assertEqual(out["skill_min_version"], "%s (raised from 0.1.0)" % bridge.SKILL_VERSION)
        self.assertEqual(read_json(self.policy_path)["skill_min_version"], bridge.SKILL_VERSION)
        policy["skill_min_version"] = "99.0.0"
        write_json(self.policy_path, policy)
        code, out = run(["--project-dir", str(self.root), "init"])
        self.assertEqual(out["policy"], "unchanged")
        self.assertEqual(read_json(self.policy_path)["skill_min_version"], "99.0.0")

    def test_v3_validation(self):
        good = read_json(self.policy_path)
        for bad, msg in (({"allowed_sources": ["x"]}, 'inside the "export" block'),
                         ({"compute": {"hpc": {"reach": "ftp"}}}, "compute.hpc.reach"),
                         ({"compute": {"bad host": {"reach": "claude-code"}}}, "compute host"),
                         ({"compute": {"-oProxyCommand": {"reach": "claude-code"}}}, "compute host"),
                         ({"skill_min_version": "latest"}, "skill_min_version"),
                         ({"policy_version": 4}, "newer"),
                         ({"export": []}, "export must be an object")):
            write_json(self.policy_path, {**good, **bad})
            code, out = run(["--project-dir", str(self.root), "status"])
            self.assertEqual(code, 1, bad)
            self.assertIn(msg, out["error"])
        write_json(self.policy_path, {**good, "privacy": "nope"})
        code, out = run(["--project-dir", str(self.root), "init"])
        self.assertEqual(code, 1)
        self.assertIn("invalid, left unchanged", out["policy"])
        self.assertEqual(read_json(self.policy_path)["privacy"], "nope")


class ReadmeSetup(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.readme = self.root / "science" / "README.md"
        self.template = (ROOT / "templates" / "README.md").read_text()

    def test_fresh_render_fills_brief_from_planning(self):
        (self.root / ".planning").mkdir()
        (self.root / ".planning" / "PROJECT.md").write_text(
            "# My Project\n\n## What This Is\n\n<!-- inferred\n     from notes -->\nA platform that stages\ncancer.\n\n"
            "Second paragraph.\n\n## Core Value\n\nCorrect numbers.\n\n## Requirements\n")
        (self.root / ".planning" / "REQUIREMENTS.md").write_text(
            "- **LBL-01** a\n- **LBL-02** b\n- **MET-01** c\nSee LBL-01 again; UTF-8 is not an ID.\n")
        code, out = run(["--project-dir", str(self.root), "init"])
        self.assertEqual(out["readme"], "created")
        text = self.readme.read_text()
        self.assertTrue(text.startswith("# Science handoff: My Project\n"))
        self.assertIn("**What the project is:** A platform that stages cancer.\n", text)
        self.assertNotIn("Second paragraph", text)
        self.assertIn("**Core value:** Correct numbers.\n", text)
        self.assertIn("**Requirement ID prefixes for `affects`:** LBL (2), MET (1)\n", text)
        self.assertIn("**Notes for Claude Science:**", text)
        self.assertNotIn("{{", text)
        self.assertEqual(bridge._split_managed(text)[1], bridge._split_managed(self.template)[1])

    def test_fresh_render_without_project_md_uses_placeholder_and_folder_name(self):
        run(["--project-dir", str(self.root), "init"])
        text = self.readme.read_text()
        self.assertTrue(text.startswith("# Science handoff: %s\n" % self.root.resolve().name))
        self.assertIn(bridge.BRIEF_PLACEHOLDER, text)
        self.assertNotIn("**What the project is:**", text)
        self.assertNotIn("Requirement ID prefixes", text)

    def test_managed_block_refresh_keeps_user_text(self):
        run(["--project-dir", str(self.root), "init"])
        before, block, after = bridge._split_managed(self.readme.read_text())
        edited = (before.replace("# Science handoff:", "# Science handoff (renamed):") + bridge.MANAGED_START
                  + "\nOLD MANAGED TEXT\n" + bridge.MANAGED_END + "\n\nMy note between.\n" + after + "\nMy brief.\n")
        self.readme.write_text(edited)
        code, out = run(["--project-dir", str(self.root), "init"])
        self.assertEqual(out["readme"], "managed-block-updated")
        text = self.readme.read_text()
        self.assertNotIn("OLD MANAGED TEXT", text)
        for kept in ("# Science handoff (renamed):", "My note between.", "My brief."):
            self.assertIn(kept, text)
        self.assertEqual(bridge._split_managed(text)[1], block)
        code, out = run(["--project-dir", str(self.root), "init"])
        self.assertEqual(out["readme"], "unchanged")

    def test_legacy_readme_migration_keeps_project_brief(self):
        (self.root / "science").mkdir()
        legacy = ("# Science handoff: Legacy Name\n\nOld intro.\n\n## Rules for Claude Science\n\n1. old rule\n\n"
                  "## Project brief\n\n**What the project is:** legacy brief text.\n\n- LBL-01 (labels)\n")
        self.readme.write_text(legacy)
        code, out = run(["--project-dir", str(self.root), "init"])
        self.assertEqual(out["readme"], "migrated-legacy")
        self.assertEqual(out["readme_backup"], "science/README.md.bak")
        self.assertEqual((self.root / "science" / "README.md.bak").read_text(), legacy)
        text = self.readme.read_text()
        self.assertTrue(text.startswith("# Science handoff: Legacy Name\n"))
        self.assertIn(bridge.MANAGED_START, text)
        self.assertTrue(text.endswith("## Project brief\n\n**What the project is:** legacy brief text.\n\n"
                                      "- LBL-01 (labels)\n"))
        self.assertNotIn("old rule", text)
        code, out = run(["--project-dir", str(self.root), "init"])
        self.assertEqual(out["readme"], "unchanged")

    def test_legacy_readme_without_brief_heading_keeps_whole_body(self):
        (self.root / "science").mkdir()
        self.readme.write_text("# My notes\n\ncustom brief\nsecond line\n")
        code, out = run(["--project-dir", str(self.root), "init"])
        self.assertEqual(out["readme"], "migrated-legacy")
        text = self.readme.read_text()
        self.assertTrue(text.endswith("## Project brief\n\ncustom brief\nsecond line\n"))
        self.assertNotIn("# My notes", text)


class ClaudeScienceSetup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_skill_zip_contents(self):
        src = self.tmp / "src" / "science-findings"
        shutil.copytree(str(bridge.CS_SKILL_DIR), str(src), ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (src / "scripts" / "__pycache__").mkdir()
        (src / "scripts" / "__pycache__" / "check_finding.cpython-39.pyc").write_bytes(b"x")
        (src / "scripts" / "stray.pyc").write_bytes(b"x")
        (src / ".DS_Store").write_bytes(b"x")
        saved = bridge.CS_SKILL_DIR
        bridge.CS_SKILL_DIR = src
        try:
            code, out = run(["skill-zip", "--out", str(self.tmp / "out" / "skill.zip")])
        finally:
            bridge.CS_SKILL_DIR = saved
        self.assertEqual(code, 0, out)
        self.assertEqual(out["skill_version"], bridge.SKILL_VERSION)
        with zipfile.ZipFile(out["zip"]) as z:
            names = z.namelist()
            self.assertIn("Skill version: %s" % bridge.SKILL_VERSION, z.read("science-findings/SKILL.md").decode())
        self.assertTrue(all(n.startswith("science-findings/") for n in names), names)
        for needed in ("science-findings/SKILL.md", "science-findings/references/format.md",
                       "science-findings/scripts/check_finding.py", "science-findings/scripts/selftest.py"):
            self.assertIn(needed, names)
            self.assertIn(needed, out["files"])
        for n in names:
            self.assertNotIn("__pycache__", n)
            self.assertFalse(n.endswith((".pyc", ".DS_Store")), n)

    def test_skill_zip_default_location(self):
        saved = tempfile.tempdir
        tempfile.tempdir = str(self.tmp)
        try:
            code, out = run(["skill-zip"])
        finally:
            tempfile.tempdir = saved
        self.assertEqual(Path(out["zip"]), self.tmp / "science-bridge" / ("science-findings-%s.zip" % bridge.SKILL_VERSION))
        self.assertTrue(Path(out["zip"]).is_file())

    def test_cs_setup_default_zip_inside_project_and_gitignored(self):
        root = (self.tmp / "proj2").resolve()
        root.mkdir()
        run(["--project-dir", str(root), "init"])
        self.assertEqual((root / "science" / ".gitignore").read_text(), ".cache/\n")
        code, out = run(["--project-dir", str(root), "cs-setup"])
        self.assertEqual(code, 0, out)
        self.assertEqual(Path(out["skill_zip"]),
                         root / "science" / ".cache" / ("science-findings-%s.zip" % bridge.SKILL_VERSION))
        self.assertTrue(Path(out["skill_zip"]).is_file())

    def test_cs_setup_grants_instructions_and_steps(self):
        root = (self.tmp / "proj").resolve()
        (root / ".planning").mkdir(parents=True)
        (root / ".planning" / "PROJECT.md").write_text("# Staging Bench\n")
        run(["--project-dir", str(root), "init"])
        code, out = run(["--project-dir", str(root), "cs-setup", "--out", str(self.tmp / "s.zip")])
        self.assertEqual(code, 0, out)
        self.assertEqual(out["project_name"], "Staging Bench")
        self.assertEqual(out["suggested_cs_project_name"], "Staging Bench (science)")
        self.assertEqual(out["grants"], [{"path": str(root), "mode": "Read-only"},
                                         {"path": str(root / "science"), "mode": "Read & write"}])
        self.assertIn(str(root), out["instructions"])
        self.assertIn("version %s or later" % bridge.SKILL_VERSION, out["instructions"])
        self.assertIn("Staging Bench", out["instructions"])
        self.assertNotIn("{{", out["instructions"])
        self.assertTrue(Path(out["skill_zip"]).is_file())
        self.assertEqual(out["skill_version"], bridge.SKILL_VERSION)
        steps = "\n".join(out["steps"])
        for phrase in ("Edit & republish", "Add skill › Upload a skill", "Add folder…", "Instructions for Claude",
                       "NEW session", "Run the science-bridge self-test", "/science-bridge:bridge verify"):
            self.assertIn(phrase, steps)
        self.assertEqual(steps.count("Add folder…"), 2)
        self.assertEqual(out["warnings"], [])

        policy = read_json(root / "science" / "policy.json")
        policy["access"] = "science-only"
        write_json(root / "science" / "policy.json", policy)
        code, out = run(["--project-dir", str(root), "cs-setup", "--out", str(self.tmp / "s.zip")])
        self.assertEqual(out["grants"], [{"path": str(root / "science"), "mode": "Read & write"}])
        self.assertIn("science-only", out["instructions"])
        self.assertNotIn("{{", out["instructions"])


@unittest.skipUnless(HAS_GIT, "git is required")
class SelfTestAndVerify(unittest.TestCase):
    def setUp(self):
        self._ssh = bridge.ssh_check
        self.root = Path(tempfile.mkdtemp()).resolve()
        (self.root / ".planning" / "research").mkdir(parents=True)
        (self.root / ".planning" / "PROJECT.md").write_text("# Demo\n\n## What This Is\n\nA demo.\n")
        (self.root / ".planning" / "research" / "questions.md").write_text(
            "# Q\n\n## For Claude Science\n\n- [ ] SQ-001 (2026-11-06, user): open one\n"
            "- [x] SQ-002 (2026-11-05, user): done — answered: x\n")
        (self.root / "src.py").write_text("print('hi')\n")
        code, out = run(["--project-dir", str(self.root), "init"])
        self.assertEqual(code, 0, out)
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "init")
        self.inbox = self.root / "science" / "inbox"
        self.selftest_path = self.inbox / "selftest.json"

    def tearDown(self):
        bridge.ssh_check = self._ssh
        os.chmod(str(self.root), 0o755)

    def selftest(self, *extra, readonly_root=True, env=None):
        if readonly_root:
            os.chmod(str(self.root), 0o555)  # what a Read-only grant of the repo root looks like
        try:
            proc = subprocess.run([sys.executable, str(SELFTEST), str(self.root)] + list(extra), capture_output=True,
                                  text=True, timeout=60, env=env)
        finally:
            os.chmod(str(self.root), 0o755)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout), read_json(self.selftest_path))
        return read_json(self.selftest_path)

    def verify(self, *extra):
        return run(["--project-dir", str(self.root), "verify"] + list(extra))

    def edit_selftest(self, **changes):
        data = read_json(self.selftest_path)
        for key, value in changes.items():
            if "__" in key:
                outer, inner = key.split("__")
                data[outer][inner] = value
            else:
                data[key] = value
        write_json(self.selftest_path, data)

    def assert_no_probes(self):
        self.assertFalse((self.root / ".science-bridge-write-probe").exists())
        self.assertFalse((self.inbox / ".selftest-probe").exists())

    @unittest.skipIf(IS_ROOT, "root ignores directory permissions")
    def test_end_to_end_ok_then_archived(self):
        data = self.selftest()
        self.assert_no_probes()
        self.assertEqual(data["format"], "science-bridge-selftest/v1")
        self.assertEqual(data["skill_version"], bridge.SKILL_VERSION)
        self.assertEqual(data["write_probe_repo_root"], "denied")
        self.assertEqual(data["inbox_write"], "ok")
        self.assertEqual(data["requests_open"], ["SQ-001"])
        self.assertEqual(data["repo"]["probe"]["path"], ".planning/PROJECT.md")
        self.assertEqual(data["repo"]["git"], "cli")
        self.assertIsNone(data["repo"]["error"])
        self.assertEqual(data["policy"]["access"], "repo-readonly")
        self.assertEqual(data["errors"], [])

        code, out = run(["--project-dir", str(self.root), "status"])
        self.assertTrue(out["selftest_pending"])
        self.assertEqual((out["pending"], out["attachments"]), ([], []), "selftest.json is not a finding")
        code, out = run(["--project-dir", str(self.root), "check"])
        self.assertEqual(code, 0, out)

        code, out = self.verify()
        self.assertEqual(code, 0, out)
        self.assertTrue(all(c["ok"] for c in out["checks"]))
        names = [c["name"] for c in out["checks"]]
        for name in ("present", "format", "fresh", "skill_version", "policy", "repo_read", "repo_head",
                     "repo_files", "repo_probe", "requests_open", "write_probe_repo_root", "inbox_write"):
            self.assertIn(name, names)
        self.assertFalse(self.selftest_path.exists())
        latest = self.root / "science" / "archive" / "selftest-latest.json"
        self.assertEqual(read_json(latest)["skill_version"], bridge.SKILL_VERSION)
        code, out = run(["--project-dir", str(self.root), "status"])
        self.assertFalse(out["selftest_pending"])
        self.assertEqual(out["last_selftest"]["skill_version"], bridge.SKILL_VERSION)
        code, out = self.verify()
        self.assertEqual((code, out["failed"]), (1, ["present"]))

    @unittest.skipIf(IS_ROOT, "root ignores directory permissions")
    def test_new_commit_after_selftest_is_fine(self):
        self.selftest()
        (self.root / "more.py").write_text("x = 1\n")
        git(self.root, "add", "more.py")
        git(self.root, "commit", "-q", "-m", "more")
        code, out = self.verify()
        self.assertEqual(code, 0, out)
        head = next(c for c in out["checks"] if c["name"] == "repo_head")
        self.assertIn("ancestor", head["detail"])

    def test_writable_repo_root_fails_and_probe_is_cleaned_up(self):
        data = self.selftest(readonly_root=False)
        self.assertEqual(data["write_probe_repo_root"], "allowed")
        self.assert_no_probes()
        code, out = self.verify()
        self.assertEqual(code, 1)
        self.assertEqual(out["failed"], ["write_probe_repo_root"])
        check = next(c for c in out["checks"] if c["name"] == "write_probe_repo_root")
        self.assertIn("Read-only", check["fix"])
        self.assertTrue(self.selftest_path.exists(), "a failed verify leaves selftest.json in the inbox")

    @unittest.skipIf(IS_ROOT, "root ignores directory permissions")
    def test_old_skill_unknown_head_and_stale_selftest_fail(self):
        self.selftest()
        self.edit_selftest(skill_version="0.1.0")
        code, out = self.verify()
        self.assertEqual(out["failed"], ["skill_version"])
        fix = next(c for c in out["checks"] if c["name"] == "skill_version")["fix"]
        self.assertIn("cs-setup", fix)

        self.selftest()
        self.edit_selftest(repo__git_head="f" * 40)
        code, out = self.verify()
        self.assertEqual(out["failed"], ["repo_head"])
        self.assertNotIn("repo_files", [c["name"] for c in out["checks"]])
        fix = next(c for c in out["checks"] if c["name"] == "repo_head")["fix"]
        self.assertIn("Read-only under Files › Artifacts › Add folder…", fix)

        self.selftest()
        old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.edit_selftest(created=old)
        code, out = self.verify()
        self.assertEqual(out["failed"], ["fresh"])
        code, out = self.verify("--max-age-hours", "48")
        self.assertEqual(code, 0, out)

    @unittest.skipIf(IS_ROOT, "root ignores directory permissions")
    def test_repo_and_requests_mismatch(self):
        self.selftest()
        self.edit_selftest(repo__error="PermissionError: denied", requests_open=["SQ-001", "SQ-009"])
        (self.root / ".planning" / "PROJECT.md").write_text("# Demo, edited\n")
        code, out = self.verify()
        self.assertEqual(sorted(out["failed"]), ["repo_probe", "repo_read", "requests_open"])
        req = next(c for c in out["checks"] if c["name"] == "requests_open")
        self.assertIn("only in Claude Science's view: SQ-009", req["detail"])

    @unittest.skipIf(IS_ROOT, "root ignores directory permissions")
    def test_selftest_respects_do_not_read(self):
        policy = read_json(self.root / "science" / "policy.json")
        policy["do_not_read"] = [".planning/*"]
        write_json(self.root / "science" / "policy.json", policy)
        project = self.root / ".planning" / "PROJECT.md"
        questions = self.root / ".planning" / "research" / "questions.md"
        os.chmod(str(project), 0)
        os.chmod(str(questions), 0)  # any attempt to open them would show up as an error
        try:
            data = self.selftest()
        finally:
            os.chmod(str(project), stat.S_IRUSR | stat.S_IWUSR)
            os.chmod(str(questions), stat.S_IRUSR | stat.S_IWUSR)
        self.assertIsNone(data["repo"]["error"])
        self.assertFalse(data["repo"]["probe"]["path"].startswith((".planning/", "science/")))
        self.assertIn("do_not_read", data["requests_error"])
        self.assertEqual(data["policy"]["do_not_read_count"], 1)
        self.assertEqual(data["errors"], [])

    @unittest.skipIf(IS_ROOT, "root ignores directory permissions")
    def test_selftest_without_git_cli_reads_dot_git(self):
        cli = self.selftest()
        empty_path = tempfile.mkdtemp()
        files = self.selftest(env={**os.environ, "PATH": empty_path})
        self.assertTrue(files["repo"]["git"].startswith("files"))
        self.assertEqual(files["repo"]["git_head"], cli["repo"]["git_head"])
        self.assertEqual(files["repo"]["tracked_files"], cli["repo"]["tracked_files"])
        code, out = self.verify()
        self.assertEqual(code, 0, out)

    @unittest.skipIf(IS_ROOT, "root ignores directory permissions")
    def test_project_without_git(self):
        shutil.rmtree(str(self.root / ".git"))
        data = self.selftest()
        self.assertIsNone(data["repo"]["git_head"])
        self.assertEqual(data["repo"]["error"], data["repo"]["git_error"])
        code, out = self.verify()
        self.assertEqual(code, 0, out)
        (self.root / "science" / "archive" / "selftest-latest.json").rename(self.selftest_path)
        self.edit_selftest(repo__error="PermissionError: x")
        code, out = self.verify()
        self.assertEqual(out["failed"], ["repo_read"])

    def test_selftest_usage_and_bad_compute_never_raise(self):
        proc = subprocess.run([sys.executable, str(SELFTEST)], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage", json.loads(proc.stdout)["errors"][0])
        data = self.selftest("--compute", "hpc=maybe", "--compute=lab=skipped:no VPN", "--compute", readonly_root=False)
        self.assertEqual(data["compute"], [{"name": "lab", "result": "skipped", "detail": "no VPN"}])
        self.assertEqual(len(data["errors"]), 2)
        missing = Path(tempfile.mkdtemp()) / "nope"
        proc = subprocess.run([sys.executable, str(SELFTEST), str(missing)], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 1)
        out = json.loads(proc.stdout)
        self.assertFalse(out["policy"]["ok"])
        self.assertIn("skipped", out["repo"]["error"])
        self.assertNotIn("Traceback", proc.stdout + proc.stderr)

    @unittest.skipIf(IS_ROOT, "root ignores directory permissions")
    def test_compute_hosts(self):
        policy = read_json(self.root / "science" / "policy.json")
        policy["compute"] = {"hpc": {"reach": "claude-code", "notes": "SLURM login node"},
                             "lab": {"reach": "claude-science", "notes": "lab VPN"}}
        write_json(self.root / "science" / "policy.json", policy)
        calls = []
        bridge.ssh_check = lambda host: (calls.append(host) or (True, "fake ok"))
        self.selftest("--compute", "lab=ok:reached it")
        code, out = self.verify()
        self.assertEqual(code, 0, out)
        self.assertEqual(calls, ["hpc"])

        bridge.ssh_check = lambda host: (False, "Permission denied (publickey)")
        self.selftest("--compute", "lab=failed:no route")
        code, out = self.verify()
        self.assertEqual(sorted(out["failed"]), ["compute:hpc", "compute:lab"])
        hpc = next(c for c in out["checks"] if c["name"] == "compute:hpc")
        self.assertIn("`! ssh hpc` once in Claude Code to log in (Claude Code reuses that login)", hpc["fix"])
        lab = next(c for c in out["checks"] if c["name"] == "compute:lab")
        self.assertIn("no route", lab["detail"])

        self.selftest()  # Claude Science did not test "lab" at all
        bridge.ssh_check = lambda host: (True, "fake ok")
        code, out = self.verify()
        self.assertEqual(out["failed"], ["compute:lab"])

    def test_compute_check_runs_ssh_with_batch_mode(self):
        policy = read_json(self.root / "science" / "policy.json")
        policy["compute"] = {"hpc": {"reach": "claude-code"}, "lab": {"reach": "claude-science"}}
        write_json(self.root / "science" / "policy.json", policy)
        fake = Path(tempfile.mkdtemp()) / "fake-ssh"
        log = fake.with_name("args.txt")
        fake.write_text('#!/bin/sh\necho "$@" > "%s"\n[ "$FAKE_SSH_FAIL" = 1 ] && { echo "Permission denied" >&2; '
                        'exit 255; }\nexit 0\n' % log)
        fake.chmod(0o755)
        old_env = dict(os.environ)
        os.environ["SCIENCE_BRIDGE_SSH"] = str(fake)
        try:
            code, out = run(["--project-dir", str(self.root), "compute-check"])
            self.assertEqual(code, 0, out)
            self.assertEqual([c["name"] for c in out["checks"]], ["compute:hpc"])
            self.assertEqual(log.read_text().split(), ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "hpc", "true"])
            os.environ["FAKE_SSH_FAIL"] = "1"
            code, out = run(["--project-dir", str(self.root), "compute-check"])
            self.assertEqual(code, 1)
            self.assertIn("Permission denied", out["checks"][0]["detail"])
            self.assertIn("! ssh hpc", out["checks"][0]["fix"])
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        policy["compute"] = {}
        write_json(self.root / "science" / "policy.json", policy)
        code, out = run(["--project-dir", str(self.root), "compute-check"])
        self.assertEqual((code, out["checks"]), (0, []))
        self.assertIn("note", out)


class ValidatorAdditions(unittest.TestCase):
    def check(self, text, name="2026-11-06-x.md"):
        p = Path(tempfile.mkdtemp()) / name
        p.write_text(text)
        return validate_finding(p)

    def test_partial(self):
        r = self.check(VALID.replace("contains_case_data: false", "contains_case_data: false\npartial: true"))
        self.assertIn("partial: true needs answers: [SQ-NNN] (the requests this finding makes progress on)", r["errors"])
        r = self.check(VALID.replace("contains_case_data: false", "contains_case_data: false\npartial: true\nanswers: [SQ-001]"))
        self.assertTrue(r["ok"], r["errors"])
        self.assertIs(r["meta"]["partial"], True)
        r = self.check(VALID.replace("contains_case_data: false", "contains_case_data: false\npartial: false"))
        self.assertTrue(r["ok"], r["errors"])
        r = self.check(VALID.replace("contains_case_data: false", "contains_case_data: false\npartial: maybe"))
        self.assertIn("partial must be true or false", r["errors"])

    def test_compute(self):
        proposal = VALID.replace("type: rule-check", "type: analysis-proposal").replace(
            "contains_case_data: false", "contains_case_data: false\ncompute: hpc.example-1")
        r = self.check(proposal)
        self.assertTrue(any("### Run spec" in e for e in r["errors"]), r["errors"])
        with_spec = proposal.replace("Table attached.", "Table attached.\n\n### Run spec\nsbatch run.sh")
        r = self.check(with_spec)
        self.assertTrue(r["ok"], r["errors"])
        self.assertEqual(r["meta"]["compute"], "hpc.example-1")
        r = self.check(with_spec.replace("type: analysis-proposal", "type: finding"))
        self.assertIn("compute is only allowed when type is analysis-proposal", r["errors"])
        r = self.check(with_spec.replace("compute: hpc.example-1", "compute: bad host!"))
        self.assertTrue(any(e.startswith("compute must be a host name") for e in r["errors"]))
        r = self.check(with_spec.replace("compute: hpc.example-1", "compute: [a, b]"))
        self.assertTrue(any(e.startswith("compute must be a host name") for e in r["errors"]))


class PartialArchive(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".planning" / "research").mkdir(parents=True)
        run(["--project-dir", str(self.root), "init"])
        bridge.ensure_requests_section(self.root)
        run(["--project-dir", str(self.root), "ask", "Review all six sites"])
        run(["--project-dir", str(self.root), "ask", "Second question"])
        self.q = self.root / ".planning" / "research" / "questions.md"

    def archive(self, name, extra):
        text = VALID.replace("affects: [LBL-01, LBL-06]", "affects: [LBL-01]\n" + extra)
        (self.root / "science" / "inbox" / name).write_text(text)
        return run(["--project-dir", str(self.root), "archive", name, "--route", "note", "--ref", "n"])

    def test_partial_appends_progress_and_does_not_tick(self):
        code, out = self.archive("2026-11-06-part-one.md", "answers: [SQ-001, SQ-404]\npartial: true")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["requests"], {"progress": ["SQ-001"], "already": [], "closed": [], "unknown": ["SQ-404"]})
        text = self.q.read_text()
        self.assertIn("- [ ] SQ-001", text)
        self.assertIn("Review all six sites — progress: science/archive/2026-11-06-part-one.md", text)
        self.assertEqual(text.count("progress:"), 1)
        record = json.loads((self.root / "science" / "archive" / "routing.jsonl").read_text().splitlines()[-1])
        self.assertEqual(record["requests"]["progress"], ["SQ-001"])
        code, out = run(["--project-dir", str(self.root), "requests"])
        self.assertEqual([r["id"] for r in out["items"]], ["SQ-001", "SQ-002"])

        code, out = self.archive("2026-11-07-part-two.md", "answers: [SQ-001]\npartial: true")
        self.assertEqual(out["requests"]["progress"], ["SQ-001"])
        self.assertEqual(self.q.read_text().count("progress:"), 2)

        code, out = self.archive("2026-11-08-final.md", "answers: [SQ-001]")
        self.assertEqual(out["requests"]["ticked"], ["SQ-001"])
        line = next(l for l in self.q.read_text().splitlines() if "SQ-001" in l)
        self.assertTrue(line.startswith("- [x] SQ-001"))
        self.assertTrue(line.endswith("answered: science/archive/2026-11-08-final.md"))
        self.assertIn("progress: science/archive/2026-11-06-part-one.md", line)

        code, out = self.archive("2026-11-09-late.md", "answers: [SQ-001]\npartial: true")
        self.assertEqual(out["requests"]["closed"], ["SQ-001"])
        code, out = run(["--project-dir", str(self.root), "ask", "review all six sites"])
        self.assertEqual(code, 1, "dedupe still sees through progress notes")


if __name__ == "__main__":
    unittest.main()


class WaitForInbox(unittest.TestCase):
    """`wait` watches science/inbox/ because Claude Science has no API."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        run(["--project-dir", str(self.root), "init"])
        self.inbox = self.root / "science" / "inbox"

    def test_times_out_with_exit_2_when_nothing_arrives(self):
        code, out = run(["--project-dir", str(self.root), "wait", "--timeout", "1.2", "--interval", "1",
                         "--settle", "0.5"])
        self.assertEqual(code, 2, out)
        self.assertTrue(out["timed_out"])
        self.assertEqual(out["arrived"], [])

    def test_reports_a_finding_written_while_waiting(self):
        import threading
        import time

        def write_later():
            time.sleep(1.2)
            (self.inbox / "2026-11-06-larynx-groups.csv").write_text("a,b\n")
            (self.inbox / "2026-11-06-larynx-stage-groups.md").write_text(VALID.replace(
                "inbox/2026-11-06-larynx-groups.csv", "inbox/2026-11-06-larynx-groups.csv"))
        threading.Thread(target=write_later).start()
        code, out = run(["--project-dir", str(self.root), "wait", "--timeout", "20", "--interval", "1",
                         "--settle", "1"])
        self.assertEqual(code, 0, out)
        self.assertFalse(out["timed_out"])
        self.assertIn("2026-11-06-larynx-stage-groups.md", out["arrived"])
        self.assertEqual([f["file"] for f in out["findings"]], ["2026-11-06-larynx-stage-groups.md"])
        self.assertIn("/science-bridge:bridge ingest", out["next"])

    def test_returns_at_once_when_findings_already_wait_unless_new_only(self):
        (self.inbox / "2026-11-06-larynx-stage-groups.md").write_text(VALID)
        code, out = run(["--project-dir", str(self.root), "wait", "--timeout", "30", "--settle", "0.5"])
        self.assertEqual(code, 0, out)
        self.assertEqual(out["arrived"], ["2026-11-06-larynx-stage-groups.md"])
        code, out = run(["--project-dir", str(self.root), "wait", "--new-only", "--timeout", "1.2",
                         "--interval", "1", "--settle", "0.5"])
        self.assertEqual(code, 2, out)

    def test_selftest_arrival_points_to_verify(self):
        (self.inbox / "selftest.json").write_text("{}")
        code, out = run(["--project-dir", str(self.root), "wait", "--timeout", "5", "--settle", "0.5"])
        self.assertEqual(code, 0, out)
        self.assertTrue(out["selftest_waiting"])
        self.assertEqual(out["next"], ["/science-bridge:bridge verify"])
