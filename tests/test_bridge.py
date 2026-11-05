"""Tests for science-bridge. Standard library only.

Run from the repo root:  python3 -m unittest discover -s tests -v
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "science-findings" / "scripts"))

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

    def set_policy(self, **overrides):
        policy = json.loads((self.sci / "policy.json").read_text())
        policy.update(overrides)
        (self.sci / "policy.json").write_text(json.dumps(policy))

    def test_init_is_idempotent_and_never_overwrites(self):
        (self.sci / "README.md").write_text("custom brief")
        code, out = run(["--project-dir", str(self.root), "init"])
        self.assertEqual(code, 0)
        self.assertEqual(out["created"], [])
        self.assertEqual((self.sci / "README.md").read_text(), "custom brief")
        for sub in ("outbox", "inbox", "archive"):
            self.assertTrue((self.sci / sub).is_dir())

    def test_phi_requires_whitelist(self):
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
        policy.update(privacy="public", do_not_read=["prereg/sealed*"])
        (self.root / "science" / "policy.json").write_text(json.dumps(policy))
        code, out = run(["--project-dir", str(self.root), "export", str(self.root / "prereg" / "sealed.md")])
        self.assertEqual(code, 1)
        self.assertTrue(any("do_not_read" in e for e in out["errors"]))


class SessionHint(unittest.TestCase):
    def hint(self, cwd):
        proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "bridge.py"), "session-hint"],
                              input=json.dumps({"cwd": str(cwd), "hook_event_name": "SessionStart"}),
                              capture_output=True, text=True, timeout=30, env={**os.environ, "CLAUDE_PROJECT_DIR": ""})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_silent_without_science_folder(self):
        self.assertEqual(self.hint(tempfile.mkdtemp()), "")

    def test_reports_pending_and_outbox_problems_from_subdir(self):
        root = Path(tempfile.mkdtemp())
        run(["--project-dir", str(root), "init"])
        self.assertEqual(self.hint(root), "")
        (root / "science" / "inbox" / "2026-11-06-larynx.md").write_text(VALID)
        (root / "science" / "outbox" / "stray.csv").write_text("x")
        (root / "src").mkdir()
        out = self.hint(root / "src")
        self.assertIn("1 Claude Science finding(s) waiting", out)
        self.assertIn("WARNING: science/outbox", out)

    def test_reports_missing_gsd_skill_in_gsd_project(self):
        root = Path(tempfile.mkdtemp())
        (root / ".planning").mkdir()
        (root / ".planning" / "config.json").write_text("{}")
        run(["--project-dir", str(root), "init", "--no-gsd"])
        self.assertIn("science-context skill is missing", self.hint(root))


if __name__ == "__main__":
    unittest.main()
