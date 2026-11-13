"""Tests for per-workstream queues, finding ownership and gated compute hosts. Standard library only.

Run from the repo root:  python3 -m unittest discover -s tests -v
"""

import datetime as _dt
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_SCRIPTS = ROOT / "cs-skill" / "science-findings" / "scripts"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(SKILL_SCRIPTS))

import bridge  # noqa: E402
import selftest  # noqa: E402


def run(argv, env=None):
    old = {k: os.environ.get(k) for k in (env or {})}
    os.environ.update(env or {})
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            code = bridge.main(argv)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    out = buf.getvalue()
    return code, json.loads(out) if out.strip() else None


def finding(answers=None, partial=False, title="A result"):
    extra = ""
    if answers:
        extra += "answers: [%s]\n" % ", ".join(answers)
    if partial:
        extra += "partial: true\n"
    return ("---\nformat: science-finding/v0.1\ntitle: %s\ntype: finding\ncreated: 2026-11-12\n"
            "author: claude-science\nconfidence: medium\nsources:\n  - https://example.org/x\n"
            "contains_case_data: false\n%s---\n\n## Summary\nS.\n\n## Details\nD.\n\n## Suggested action\nA.\n"
            % (title, extra))


class Base(unittest.TestCase):
    WS = ("ideas", "measure", "train")

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".planning").mkdir()
        (self.root / ".planning" / "config.json").write_text("{}\n")
        for ws in self.WS:
            (self.root / ".planning" / "workstreams" / ws).mkdir(parents=True)
        os.environ.pop(bridge.WS_ENV, None)

    def cli(self, *argv, env=None):
        return run(["--project-dir", str(self.root)] + list(argv), env=env)

    def policy(self):
        return json.loads((self.root / "science" / "policy.json").read_text())

    def write_policy(self, policy):
        (self.root / "science" / "policy.json").write_text(json.dumps(policy, indent=2))

    def inbox(self, name, text):
        (self.root / "science" / "inbox" / name).write_text(text)


class WithoutWorkstreams(Base):
    def test_plain_init_suggests_but_does_not_enable(self):
        code, out = self.cli("init")
        self.assertEqual(code, 0, out)
        self.assertIn("--workstreams-default", out["workstreams"])
        self.assertNotIn("workstreams", self.policy())
        self.assertEqual(self.policy()["policy_version"], 3)
        self.assertEqual(self.policy()["skill_min_version"], bridge.BASE_SKILL_VERSION)
        skill = self.root / bridge.GSD_SKILL_ENTRY / "SKILL.md"
        self.assertEqual(skill.read_bytes(), bridge.GSD_SKILL_TEMPLATE.read_bytes())
        self.assertTrue((self.root / bridge.QUESTIONS_REL).is_file())
        code, out = self.cli("ask", "Q?", "--ws", "train")
        self.assertEqual(code, 1)
        self.assertIn("no workstreams block", out["error"])

    def test_no_workstream_dirs_means_no_note(self):
        root = Path(tempfile.mkdtemp())
        code, out = run(["--project-dir", str(root), "init"])
        self.assertEqual(code, 0, out)
        self.assertNotIn("workstreams", out)


class Enabled(Base):
    def setUp(self):
        super().setUp()
        code, out = self.cli("init", "--workstreams-default", "ideas")
        self.assertEqual(code, 0, out)
        self.out = out

    def test_init_writes_v4_block_and_raises_skill(self):
        policy = self.policy()
        self.assertEqual(policy["policy_version"], 4)
        self.assertEqual(policy["skill_min_version"], bridge.WS_SKILL_VERSION)
        self.assertTrue(self.out["skill_min_version"].startswith(bridge.WS_SKILL_VERSION), self.out["skill_min_version"])
        self.assertEqual(policy["workstreams"], {"enabled": True, "default": "ideas",
                                                 "queue": bridge.WS_QUEUE_DEFAULT})
        self.assertFalse((self.root / bridge.QUESTIONS_REL).exists(), "no shared queue is created")
        code, again = self.cli("init", "--workstreams-default", "ideas")
        self.assertEqual(code, 0, again)
        self.assertIn("unchanged", again["workstreams"])
        self.assertEqual(again["policy"], "unchanged")

    def test_init_refuses_unknown_default(self):
        code, out = self.cli("init", "--workstreams-default", "nope")
        self.assertEqual(code, 1)
        self.assertIn("no such workstream", out["error"])

    def test_science_context_is_rendered_for_workstreams(self):
        text = (self.root / bridge.GSD_SKILL_ENTRY / "SKILL.md").read_text()
        self.assertIn(".planning/workstreams/<ws>/questions.md", text)
        self.assertIn("routing-*.jsonl", text)
        self.assertIn(".planning/workstreams/*/questions.md", text)
        self.assertNotIn("Open `.planning/research/questions.md`", text)
        code, out = self.cli("status")
        self.assertEqual(out["gsd"]["skill"], "current")

    def test_readme_and_instructions_name_the_queues(self):
        readme = (self.root / "science" / "README.md").read_text()
        self.assertIn(".planning/workstreams/{ws}/questions.md", readme)
        self.assertIn("routing-<workstream>.jsonl", readme)
        code, out = self.cli("cs-setup", "--out", str(self.root / "s.zip"))
        self.assertEqual(code, 0, out)
        self.assertIn(".planning/workstreams/{ws}/questions.md", out["instructions"])
        self.assertIn("one workstream only", out["instructions"])
        self.assertIn("version %s or later" % bridge.WS_SKILL_VERSION, out["instructions"])

    def test_policy_validation(self):
        good = self.policy()
        for bad, msg in (({"policy_version": 3}, "need policy_version 4"),
                         ({"workstreams": {"enabled": True}}, "workstreams.default"),
                         ({"workstreams": {"default": "ideas", "queue": "q.md"}}, "{ws}"),
                         ({"workstreams": {"default": "ideas", "queue": "/abs/{ws}.md"}}, "{ws}"),
                         ({"workstreams": {"default": "ideas", "queue": "../{ws}.md"}}, "{ws}"),
                         ({"workstreams": []}, "must be an object"),
                         ({"compute": {"hpc": {"reach": "claude-code", "requires": "x"}}}, "requires"),
                         ({"compute": {"hpc": {"reach": "claude-code", "requires": ["../x"]}}}, "requires"),
                         ({"compute": {"hpc": {"reach": "claude-code", "submit_via": 3}}}, "submit_via")):
            self.write_policy({**good, **bad})
            code, out = self.cli("status")
            self.assertEqual(code, 1, bad)
            self.assertIn(msg, out["error"])
        self.write_policy({**good, "workstreams": {"enabled": False}})
        self.assertEqual(self.cli("status")[0], 0, "a disabled block needs no default")

    def test_ask_numbers_across_queues_and_needs_ws(self):
        code, out = self.cli("ask", "First?")
        self.assertEqual(code, 1)
        self.assertIn("pass --ws", out["error"])
        code, a = self.cli("ask", "First?", "--ws", "train")
        self.assertEqual((code, a["id"], a["file"]), (0, "SQ-001", ".planning/workstreams/train/questions.md"))
        code, b = self.cli("ask", "Second?", env={bridge.WS_ENV: "measure"})
        self.assertEqual((code, b["id"], b["workstream"]), (0, "SQ-002", "measure"))
        code, c = run(["--project-dir", str(self.root), "--ws", "train", "ask", "Third?"])
        self.assertEqual((code, c["id"]), (0, "SQ-003"))
        code, dup = self.cli("ask", "first?", "--ws", "ideas")
        self.assertEqual(code, 1)
        self.assertIn("SQ-001 (train)", dup["error"])
        code, bad = self.cli("ask", "Q?", "--ws", "nope")
        self.assertIn("unknown workstream", bad["error"])
        text = (self.root / ".planning/workstreams/train/questions.md").read_text()
        self.assertIn("# Questions for Claude Science (train)", text)
        self.assertIn("## For Claude Science", text)
        code, req = self.cli("requests", "--ws", "train")
        self.assertEqual([r["id"] for r in req["items"]], ["SQ-001", "SQ-003"])
        code, req = self.cli("requests")
        self.assertEqual(sorted(r["id"] for r in req["items"]), ["SQ-001", "SQ-002", "SQ-003"])
        self.assertEqual(req["file"], bridge.WS_QUEUE_DEFAULT)

    def test_finding_ownership_and_archive(self):
        self.cli("ask", "Train question?", "--ws", "train")          # SQ-001
        self.cli("ask", "Measure question?", "--ws", "measure")      # SQ-002
        self.inbox("2026-11-12-train.md", finding(["SQ-001"]))
        self.inbox("2026-11-12-loose.md", finding())
        self.inbox("2026-11-12-mixed.md", finding(["SQ-001", "SQ-002"]))
        code, st = self.cli("status", "--ws", "train")
        self.assertEqual([p["file"] for p in st["pending"]], ["2026-11-12-train.md"])
        self.assertEqual(st["workstreams"]["pending_by_workstream"], {"train": 1, "ideas": 1, "unsettled": 1})
        code, st = self.cli("status", "--ws", "ideas")
        self.assertEqual(sorted(p["file"] for p in st["pending"]), ["2026-11-12-loose.md", "2026-11-12-mixed.md"])

        code, out = self.cli("archive", "2026-11-12-train.md", "--route", "note", "--ref", "n", "--ws", "ideas")
        self.assertEqual(code, 1)
        self.assertIn("belongs to workstream train", out["error"])
        code, out = self.cli("archive", "2026-11-12-train.md", "--route", "note", "--ref", "n")
        self.assertIn("pass --ws", out["error"])
        code, out = self.cli("archive", "2026-11-12-train.md", "--route", "note", "--ref", "n", "--ws", "train")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["requests"]["ticked"], ["SQ-001"])
        self.assertEqual(out["log"], "science/archive/routing-train.jsonl")
        self.assertIn("- [x] SQ-001", (self.root / ".planning/workstreams/train/questions.md").read_text())
        self.assertFalse((self.root / "science/archive/routing.jsonl").exists())

        code, out = self.cli("archive", "2026-11-12-loose.md", "--route", "seed", "--ref", "s", "--ws", "train")
        self.assertIn("belongs to workstream ideas", out["error"])
        code, out = self.cli("archive", "2026-11-12-loose.md", "--route", "seed", "--ref", "s", "--ws", "ideas")
        self.assertEqual(code, 0, out)

        code, out = self.cli("archive", "2026-11-12-mixed.md", "--route", "note", "--ref", "n", "--ws", "ideas")
        self.assertEqual(code, 1)
        self.assertIn("several workstreams", out["error"])
        code, out = self.cli("archive", "2026-11-12-mixed.md", "--route", "rejected", "--ref", "x", "--ws", "train")
        self.assertEqual(code, 1, "only the default workstream may reject a mixed finding")
        code, out = self.cli("archive", "2026-11-12-mixed.md", "--route", "rejected", "--ref", "x", "--ws", "ideas")
        self.assertEqual(code, 0, out)
        self.assertNotIn("requests", out)
        self.assertIn("- [ ] SQ-002", (self.root / ".planning/workstreams/measure/questions.md").read_text())

    def test_partial_answer_notes_progress_in_owner_queue(self):
        self.cli("ask", "Big question?", "--ws", "measure")
        self.inbox("2026-11-12-part.md", finding(["SQ-001"], partial=True))
        code, out = self.cli("archive", "2026-11-12-part.md", "--route", "note", "--ref", "n", "--ws", "measure")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["requests"]["progress"], ["SQ-001"])

    def test_selftest_reads_every_queue_and_verify_agrees(self):
        self.cli("ask", "One?", "--ws", "train")
        self.cli("ask", "Two?", "--ws", "measure")
        self.cli("ask", "Three?", "--ws", "measure")
        self.inbox("2026-11-12-two.md", finding(["SQ-002"]))
        self.cli("archive", "2026-11-12-two.md", "--route", "note", "--ref", "n", "--ws", "measure")
        info, blocker = selftest.read_policy(self.root)
        found, err = selftest.open_requests(self.root, blocker, info.get("workstreams"))
        self.assertIsNone(err)
        self.assertEqual(sorted(found), ["SQ-001", "SQ-003"])
        local = sorted(r["id"] for r in bridge.list_requests(self.root) if r["open"])
        self.assertEqual(sorted(found), local)
        blocked = selftest.Blocker([".planning/workstreams/train/*"])
        found, err = selftest.open_requests(self.root, blocked, info.get("workstreams"))
        self.assertEqual(found, ["SQ-003"])
        self.assertIn("do_not_read", err)

    def test_consult_status_and_sent_respect_workstream(self):
        self.cli("ask", "Train?", "--ws", "train")
        self.cli("ask", "Ideas?", "--ws", "ideas")
        env = {bridge.CS_CLI_ENV: "none"}
        code, out = self.cli("consult", "--ws", "train", env=env)
        self.assertEqual([u["id"] for u in out["unsent"]], ["SQ-001"])
        code, out = self.cli("consult", "sent", "SQ-001", "SQ-002", "--ws", "train", env=env)
        self.assertEqual(code, 1)
        self.assertEqual(out["recorded"], ["SQ-001"])
        self.assertEqual(out["other_workstreams"], ["SQ-002 (ideas)"])
        code, out = self.cli("consult", "sent", "SQ-002", env=env)
        self.assertIn("pass --ws", out["error"])

    def test_hook_reminders_are_per_session(self):
        self.cli("consult", "on", env={bridge.CS_CLI_ENV: "none"})
        self.cli("ask", "Train?", "--ws", "train")
        now = _dt.datetime(2026, 11, 12, 12, 0, tzinfo=_dt.timezone.utc)

        def hook(sid, minutes=0):
            return bridge.consult_hook({"cwd": str(self.root), "hook_event_name": "PostToolUse", "tool_name": "Skill",
                                        "session_id": sid}, now + _dt.timedelta(minutes=minutes))
        first = hook("s-train")
        self.assertIsNotNone(first)
        text = first["hookSpecificOutput"]["additionalContext"]
        self.assertIn("SQ-001 [train]", text)
        self.assertIn("act only on the questions and findings of the workstream", text)
        self.assertIsNone(hook("s-train", 1), "same session, same work: not repeated")
        self.assertIsNotNone(hook("s-ideas", 1), "another session still gets it")
        state = bridge.load_consult_state(self.root)
        self.assertEqual(sorted(state["sessions"]), ["s-ideas", "s-train"])
        hook("s-new", 60 * 72)
        self.assertEqual(sorted(bridge.load_consult_state(self.root)["sessions"]), ["s-new"], "old sessions pruned")

    def test_session_hint_counts_by_workstream(self):
        self.cli("ask", "Train?", "--ws", "train")
        self.inbox("2026-11-12-t.md", finding(["SQ-001"]))
        hints = "\n".join(filter(None, (h(self.root) for h in (bridge._hint_findings, bridge._hint_requests))))
        self.assertIn("by workstream: train 1", hints)
        self.assertIn("ingest --ws <workstream>", hints)


class ReviewFindings(Base):
    """Regressions for the 0.7.0 review: the shared queue from before, duplicate ids, consult state, env workstream."""

    def test_opting_in_keeps_the_old_shared_queue(self):
        self.cli("init")
        code, old = self.cli("ask", "Old shared question?")
        self.assertEqual(old["id"], "SQ-001")
        self.cli("consult", "sent", "SQ-001", env={bridge.CS_CLI_ENV: "none"})
        code, out = self.cli("init", "--workstreams-default", "ideas")
        self.assertEqual(code, 0, out)
        code, new = self.cli("ask", "New question?", "--ws", "train")
        self.assertEqual(new["id"], "SQ-002", "numbering continues after the old queue")
        code, req = self.cli("requests", "--ws", "ideas")
        self.assertEqual([(r["id"], r["file"]) for r in req["items"]], [("SQ-001", bridge.QUESTIONS_REL)])
        self.inbox("2026-11-12-old.md", finding(["SQ-001"]))
        code, out = self.cli("archive", "2026-11-12-old.md", "--route", "note", "--ref", "n", "--ws", "ideas")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["requests"]["ticked"], ["SQ-001"])
        self.assertIn("- [x] SQ-001", (self.root / bridge.QUESTIONS_REL).read_text())
        info, blocker = selftest.read_policy(self.root)
        self.assertIn(bridge.QUESTIONS_REL, selftest.queue_rels(self.root, blocker, info["workstreams"]))

    def test_duplicate_ids_block_routing_and_are_reported(self):
        self.cli("init", "--workstreams-default", "ideas")
        for ws in ("train", "measure"):
            q = self.root / ".planning" / "workstreams" / ws / "questions.md"
            q.write_text("# Q\n\n## For Claude Science\n\n- [ ] SQ-007 (2026-11-12, agent): %s? \n" % ws)
        code, st = self.cli("status")
        self.assertEqual(sorted(st["workstreams"]["duplicate_sq_ids"]["SQ-007"]),
                         [".planning/workstreams/measure/questions.md", ".planning/workstreams/train/questions.md"])
        self.inbox("2026-11-12-dup.md", finding(["SQ-007"]))
        for ws in ("train", "measure"):
            code, out = self.cli("archive", "2026-11-12-dup.md", "--route", "note", "--ref", "n", "--ws", ws)
            self.assertEqual(code, 1)
            self.assertIn("more than one queue", out["error"])
        checks = bridge.selftest_checks(self.root, bridge.load_policy(self.root), {}, 24)
        dup = next(c for c in checks if c["name"] == "sq_ids_unique")
        self.assertFalse(dup["ok"])

    def test_hook_without_news_does_not_rewrite_state(self):
        self.cli("init", "--workstreams-default", "ideas")
        self.cli("consult", "on", env={bridge.CS_CLI_ENV: "none"})
        state = self.root / bridge.CONSULT_STATE_REL
        payload = {"cwd": str(self.root), "hook_event_name": "PostToolUse", "tool_name": "Skill", "session_id": "s1"}
        self.assertIsNone(bridge.consult_hook(payload))
        self.assertFalse(state.exists(), "nothing to note, nothing written")
        self.cli("ask", "Q?", "--ws", "train")
        self.cli("consult", "sent", "SQ-001", "--ws", "train", env={bridge.CS_CLI_ENV: "none"})
        self.assertIsNone(bridge.consult_hook(payload), "sent and awaiting: nothing to remind")
        self.assertIn("SQ-001", bridge.load_consult_state(self.root)["sent"], "the hook kept the sent record")
        self.assertEqual(bridge.load_consult_state(self.root)["sessions"], {})

    def test_hook_uses_gsd_workstream_env(self):
        self.cli("init", "--workstreams-default", "ideas")
        self.cli("consult", "on", env={bridge.CS_CLI_ENV: "none"})
        self.cli("ask", "Train only?", "--ws", "train")
        gate = {"cwd": str(self.root), "hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
                "session_id": "s-measure", "tool_input": {"questions": [{"question": "Pick SQ-999?"}]}}
        os.environ[bridge.WS_ENV] = "measure"
        try:
            self.assertIsNone(bridge.consult_hook(gate), "another workstream's item doesn't block this session")
            os.environ[bridge.WS_ENV] = "train"
            gate["session_id"] = "s-train"
            out = bridge.consult_hook(gate)
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        finally:
            os.environ.pop(bridge.WS_ENV, None)

    def test_disabled_block_silences_the_note_and_default_must_exist(self):
        self.cli("init")
        policy = self.policy()
        policy["workstreams"] = {"enabled": False}
        self.write_policy(policy)
        code, out = self.cli("init")
        self.assertNotIn("workstreams", out)
        code, out = self.cli("init", "--workstreams-default", "ghost")
        self.assertIn("no such workstream", out["error"])
        empty = Path(tempfile.mkdtemp())
        run(["--project-dir", str(empty), "init"])
        code, out = run(["--project-dir", str(empty), "init", "--workstreams-default", "ideas"])
        self.assertEqual(code, 1)
        self.assertIn("found: none", out["error"])

    def test_wait_only_wakes_for_its_workstream(self):
        self.cli("init", "--workstreams-default", "ideas")
        self.cli("ask", "Train?", "--ws", "train")
        self.inbox("2026-11-12-t.md", finding(["SQ-001"]))
        code, out = self.cli("wait", "--ws", "measure", "--timeout", "1", "--interval", "1", "--settle", "0")
        self.assertEqual(code, 2, out)
        code, out = self.cli("wait", "--ws", "train", "--timeout", "1", "--interval", "1", "--settle", "0")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["next"], ["/science-bridge:bridge ingest --ws train"])


class Deliver(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / ".planning").mkdir()
        run(["--project-dir", str(self.root), "init"])

    def cli(self, *argv):
        return run(["--project-dir", str(self.root)] + list(argv), env={bridge.CS_CLI_ENV: "none"})

    def test_default_is_manual(self):
        code, out = self.cli("ask", "Q?")
        self.assertEqual((out["deliver"], out["next"]), ("manual", 'the user says "process the open SQ requests" '
                                                                   'in Claude Science'))
        code, st = self.cli("status")
        self.assertEqual(st["consult"]["deliver"], "manual")

    def test_chrome_without_auto_consult(self):
        code, out = self.cli("consult", "deliver", "chrome")
        self.assertEqual((code, out["deliver"]), (0, "chrome"), out)
        policy = json.loads((self.root / "science" / "policy.json").read_text())
        self.assertEqual(policy["consult"], {"deliver": "chrome"})
        code, out = self.cli("ask", "Q?")
        self.assertEqual(out["deliver"], "chrome")
        self.assertIn("without asking the user", out["next"])
        self.assertIn("Claude Code sends questions to Claude Science itself", bridge._hint_requests(self.root))
        payload = {"cwd": str(self.root), "hook_event_name": "PostToolUse", "tool_name": "Skill", "session_id": "s"}
        self.assertIsNone(bridge.consult_hook(payload), "no auto-consult hooks")
        code, out = self.cli("consult", "deliver", "manual")
        self.assertEqual(out["deliver"], "manual")
        code, out = self.cli("consult", "deliver", "pigeon")
        self.assertEqual(code, 1)

    def test_auto_consult_implies_chrome_and_policy_validates(self):
        self.cli("consult", "on")
        self.assertEqual(self.cli("status")[1]["consult"]["deliver"], "chrome")
        path = self.root / "science" / "policy.json"
        policy = json.loads(path.read_text())
        policy["consult"]["deliver"] = "fax"
        path.write_text(json.dumps(policy))
        code, out = self.cli("status")
        self.assertIn("consult.deliver", out["error"])


class ComputeGate(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        run(["--project-dir", str(self.root), "init"])
        path = self.root / "science" / "policy.json"
        policy = json.loads(path.read_text())
        policy["compute"] = {"hpc": {"reach": "claude-code", "requires": ["handoffs/ready.md"],
                                    "submit_via": "tools/submit.sh"}}
        path.write_text(json.dumps(policy))

    def test_gated_until_required_file_exists(self):
        fake = self.root / "fake-ssh"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        env = {"SCIENCE_BRIDGE_SSH": str(fake)}
        code, out = run(["--project-dir", str(self.root), "compute-check"], env=env)
        self.assertEqual(code, 0, out)
        self.assertEqual(out["gated"], ["hpc"])
        self.assertIn("waits for handoffs/ready.md", out["checks"][0]["detail"])
        code, st = run(["--project-dir", str(self.root), "status"])
        self.assertEqual(st["compute_gated"], {"hpc": ["handoffs/ready.md"]})
        (self.root / "handoffs").mkdir()
        (self.root / "handoffs" / "ready.md").write_text("ready\n")
        code, out = run(["--project-dir", str(self.root), "compute-check"], env=env)
        self.assertEqual(code, 0, out)
        self.assertNotIn("gated", out)
        self.assertIn("succeeded", out["checks"][0]["detail"])

    def test_skill_zip_name_is_a_valid_skill_id(self):
        # Claude Science names a newly uploaded skill after the zip file and refuses dots.
        code, out = run(["--project-dir", str(self.root), "cs-setup"])
        self.assertRegex(Path(out["skill_zip"]).stem, r"^[a-z0-9-]+$")

    def test_instructions_mention_gating_only_when_used(self):
        code, out = run(["--project-dir", str(self.root), "cs-setup", "--out", str(self.root / "s.zip")])
        self.assertIn('"requires" paths', out["instructions"])
        root = Path(tempfile.mkdtemp())
        run(["--project-dir", str(root), "init"])
        code, out = run(["--project-dir", str(root), "cs-setup", "--out", str(root / "s.zip")])
        self.assertNotIn('"requires"', out["instructions"])
        template = (ROOT / "templates" / "cs-instructions.txt").read_text()
        self.assertIn(bridge.CS_QUEUE_SENTENCE, template)
        self.assertIn(bridge.CS_COMPUTE_SENTENCE, template)


if __name__ == "__main__":
    unittest.main()
