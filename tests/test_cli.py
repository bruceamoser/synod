"""CLI tests: argparse surface, the scripted end-to-end run, and the refusals.

Loads scripts/council.py via importlib (the engine is a single file, not a
package) and drives it in-process through engine.main(). COUNCILS_ROOT is
patched to a temp dir so the real ~/.hermes is never touched.
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from _engine import (CORE_CHARTER_MEMBERS, engine, finding, ruling,
                     write_charter)

ALL_COMMANDS = [
    "scaffold", "record-brief", "add-source", "finding", "note-round",
    "brief", "verify", "check", "judge-brief", "seal-ruling", "close",
    "validate-charter", "register", "deregister", "list", "show",
]


def cli(argv, root):
    """Run engine.main(argv) with COUNCILS_ROOT -> root.

    Returns (exit_code, stdout, stderr). Every non-zero exit carries a JSON
    {"error": ...} object on stdout; success carries the command's result.
    """
    out, err = io.StringIO(), io.StringIO()
    with patch.object(engine, "COUNCILS_ROOT", Path(root)), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = engine.main(list(argv))
        except SystemExit as e:
            code = e.code
    return (code if isinstance(code, int) else 1), out.getvalue(), err.getvalue()


def parse_json(text):
    return json.loads(text)


class HelpTest(unittest.TestCase):
    def test_top_level_help_lists_every_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = cli(["--help"], tmp)
            self.assertEqual(code, 0)
            for cmd in ALL_COMMANDS:
                self.assertIn(cmd, out)

    def test_subcommand_help_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            for cmd in ALL_COMMANDS:
                code, out, err = cli([cmd, "--help"], tmp)
                self.assertEqual(code, 0, f"{cmd} --help: {err}")

    def test_no_command_prints_help_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = cli([], tmp)
            self.assertEqual(code, 1)
            self.assertIn("scaffold", err)  # help goes to stderr
            self.assertEqual(parse_json(out), {"error": "no command given"})

    def test_unknown_command_is_usage_error_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = cli(["frobnicate"], tmp)
            self.assertEqual(code, 1)
            self.assertIn("error", parse_json(out))


class ScaffoldTest(unittest.TestCase):
    def test_missing_core_role_exits_1_and_names_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            charter = write_charter(tmp, members=[
                {"role": "librarian"}, {"role": "judge"},
                {"role": "researcher"},  # contrarian missing
            ])
            code, out, err = cli(["scaffold", str(charter)], tmp)
            self.assertEqual(code, 1)
            msg = parse_json(out)["error"]
            self.assertIn("contrarian", msg)
            # the run dir must NOT have been created
            self.assertEqual(list(Path(tmp).iterdir()) and
                             [p.name for p in Path(tmp).iterdir()],
                             ["charter.yaml"])

    def test_duplicate_role_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            charter = write_charter(tmp, members=[
                {"role": "librarian"}, {"role": "judge"},
                {"role": "contrarian"}, {"role": "contrarian"},
            ])
            code, out, err = cli(["scaffold", str(charter)], tmp)
            self.assertEqual(code, 1)
            self.assertIn("contrarian", parse_json(out)["error"])

    def test_empty_name_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            charter = write_charter(tmp, name="")
            code, out, err = cli(["scaffold", str(charter)], tmp)
            self.assertEqual(code, 1)
            self.assertIn("name", parse_json(out)["error"])

    def test_quorum_above_voting_members_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            charter = write_charter(tmp, consensus={
                "quorum": 5, "max_rounds": 3, "no_progress_limit": 2})
            code, out, err = cli(["scaffold", str(charter)], tmp)
            self.assertEqual(code, 1)
            self.assertIn("quorum", parse_json(out)["error"])

    def test_scaffold_creates_dirs_and_charter_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            charter = write_charter(tmp)
            code, out, err = cli(["scaffold", str(charter)], tmp)
            self.assertEqual(code, 0, err)
            info = parse_json(out)
            run = Path(info["run"])
            self.assertTrue(run.is_dir())
            self.assertEqual(info["charter"], "test-council")
            self.assertEqual(info["events"], 1)
            for sub in ("briefs", "judge", "sources"):
                self.assertTrue((run / sub).is_dir())
            events = engine.read_events(run)
            self.assertEqual(events[0]["type"], "charter")
            self.assertEqual(events[0]["provenance"]["role"], "engine")
            self.assertEqual(events[0]["provenance"]["model"], None)

    def test_scaffold_with_problem_file_records_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            charter = write_charter(tmp)
            problem = Path(tmp) / "problem.txt"
            problem.write_text("Decide whether to ship by Friday.\n")
            code, out, err = cli(
                ["scaffold", str(charter), "--problem-file", str(problem)], tmp)
            self.assertEqual(code, 0, err)
            info = parse_json(out)
            run = Path(info["run"])
            self.assertEqual(info["events"], 2)
            self.assertEqual((run / "problem.md").read_text(),
                             "Decide whether to ship by Friday.\n")
            events = engine.read_events(run)
            self.assertEqual(events[1]["type"], "problem")
            self.assertEqual(events[1]["provenance"]["role"], "engine")
            self.assertEqual(events[1]["payload"]["sources"], [])

    def test_scaffold_empty_problem_file_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            charter = write_charter(tmp)
            problem = Path(tmp) / "empty.txt"
            problem.write_text("   \n  ")
            code, out, err = cli(
                ["scaffold", str(charter), "--problem-file", str(problem)], tmp)
            self.assertEqual(code, 1)
            self.assertIn("empty", parse_json(out)["error"])

    def test_second_scaffold_same_minute_gets_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            charter = write_charter(tmp)
            code1, out1, _ = cli(["scaffold", str(charter)], tmp)
            code2, out2, _ = cli(["scaffold", str(charter)], tmp)
            self.assertEqual(code1, 0)
            self.assertEqual(code2, 0)
            self.assertNotEqual(Path(parse_json(out1)["run"]),
                                Path(parse_json(out2)["run"]))
            self.assertTrue(parse_json(out2)["run"].endswith("-2"))


class RecordBriefTest(unittest.TestCase):
    def _run_with_charter(self, tmp):
        charter = write_charter(tmp)
        code, out, err = cli(["scaffold", str(charter)], tmp)
        self.assertEqual(code, 0, err)
        return Path(parse_json(out)["run"])

    def test_empty_file_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_with_charter(tmp)
            brief = Path(tmp) / "brief.txt"
            brief.write_text("   ")
            code, out, err = cli(["record-brief", str(run), "--file", str(brief)], tmp)
            self.assertEqual(code, 1)
            self.assertIn("empty", parse_json(out)["error"])
            self.assertFalse((run / "problem.md").exists())

    def test_second_record_brief_exits_1_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_with_charter(tmp)
            brief = Path(tmp) / "brief.txt"
            brief.write_text("The problem, stated plainly for the council.")
            code, out, err = cli(["record-brief", str(run), "--file", str(brief)], tmp)
            self.assertEqual(code, 0, err)
            code, out, err = cli(["record-brief", str(run), "--file", str(brief)], tmp)
            self.assertEqual(code, 1)
            self.assertIn("already recorded", parse_json(out)["error"])
            # still exactly one problem event
            problems = [e for e in engine.read_events(run) if e["type"] == "problem"]
            self.assertEqual(len(problems), 1)
            self.assertEqual(problems[0]["provenance"]["role"], "librarian")

    def test_record_brief_writes_problem_md_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_with_charter(tmp)
            text = "Line one.\nLine two with  double spaces.\n"
            brief = Path(tmp) / "brief.txt"
            brief.write_text(text)
            code, out, err = cli(["record-brief", str(run), "--file", str(brief)], tmp)
            self.assertEqual(code, 0, err)
            self.assertEqual((run / "problem.md").read_text(), text)


class AddSourceTest(unittest.TestCase):
    def _run_with_charter(self, tmp):
        charter = write_charter(tmp)
        code, out, err = cli(["scaffold", str(charter)], tmp)
        self.assertEqual(code, 0, err)
        return Path(parse_json(out)["run"])

    def test_add_source_default_label_and_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_with_charter(tmp)
            src = Path(tmp) / "notes.txt"
            src.write_text("Some source material for the researcher to cite.")
            code, out, err = cli(["add-source", str(run), "--file", str(src)], tmp)
            self.assertEqual(code, 0, err)
            info = parse_json(out)
            self.assertEqual(info["label"], "notes")
            self.assertTrue((run / "sources" / "notes").is_file())
            digests = [e for e in engine.read_events(run)
                       if e["type"] == "digest" and e["payload"]["kind"] == "source"]
            self.assertEqual(len(digests), 1)
            self.assertEqual(digests[0]["payload"]["char_count"],
                             len("Some source material for the researcher to cite."))
            self.assertEqual(len(digests[0]["payload"]["sha256"]), 64)
            self.assertEqual(digests[0]["provenance"]["role"], "librarian")

    def test_add_source_label_collision_appends_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_with_charter(tmp)
            a = Path(tmp) / "a.txt"
            a.write_text("first")
            b = Path(tmp) / "b.txt"
            b.write_text("second")
            for f, want in ((a, "x"), (b, "x-2")):
                code, out, err = cli(
                    ["add-source", str(run), "--file", str(f), "--label", "x"], tmp)
                self.assertEqual(code, 0, err)
                self.assertEqual(parse_json(out)["label"], want)


class BriefTest(unittest.TestCase):
    def _run_with_problem(self, tmp):
        charter = write_charter(tmp)
        code, out, err = cli(["scaffold", str(charter)], tmp)
        self.assertEqual(code, 0, err)
        run = Path(parse_json(out)["run"])
        brief = Path(tmp) / "brief.txt"
        brief.write_text("Should we ship the beta by Friday or hold for next sprint?")
        code, out, err = cli(["record-brief", str(run), "--file", str(brief)], tmp)
        self.assertEqual(code, 0, err)
        return run

    def test_judge_role_refused_with_wall_error_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_with_problem(tmp)
            code, out, err = cli(
                ["brief", str(run), "--round", "1", "--role", "judge"], tmp)
            self.assertEqual(code, 1)
            self.assertEqual(parse_json(out),
                             {"error": "wall: judge never receives a briefing packet"})
            self.assertFalse((run / "briefs" / "round-01" / "judge.json").exists())

    def test_non_member_role_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_with_problem(tmp)
            code, out, err = cli(
                ["brief", str(run), "--round", "1", "--role", "game-architect"], tmp)
            self.assertEqual(code, 1)
            self.assertIn("not a member", parse_json(out)["error"])

    def test_no_problem_recorded_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            charter = write_charter(tmp)
            code, out, err = cli(["scaffold", str(charter)], tmp)
            self.assertEqual(code, 0, err)
            run = Path(parse_json(out)["run"])
            code, out, err = cli(
                ["brief", str(run), "--round", "1", "--role", "researcher"], tmp)
            self.assertEqual(code, 1)
            self.assertIn("record-brief", parse_json(out)["error"])

    def test_brief_round1_contains_no_findings_and_correct_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_with_problem(tmp)
            code, out, err = cli(
                ["brief", str(run), "--round", "1", "--role", "contrarian"], tmp)
            self.assertEqual(code, 0, err)
            info = parse_json(out)
            self.assertEqual(info["role"], "contrarian")
            self.assertEqual(info["round"], 1)
            packet = json.loads(Path(info["brief"]).read_text())
            self.assertEqual(packet["council"], "test-council")
            self.assertEqual(packet["role"], "contrarian")
            self.assertEqual(packet["ledger_view"]["positions"], {})
            self.assertEqual(packet["ledger_view"]["open_refutes"], [])
            self.assertEqual(packet["ledger_view"]["sealed_rulings"], [])
            self.assertIn("counter-example", packet["task"])
            self.assertEqual(
                Path(info["brief"]).name, "contrarian.json")
            self.assertIn("round-01", Path(info["brief"]).parts)

    def test_brief_round2_shows_round1_positions_and_refutes(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_with_problem(tmp)
            for role, stance in (("researcher", "support"),
                                 ("contrarian", "refute")):
                f = Path(tmp) / f"f-{role}.json"
                f.write_text(json.dumps(finding(role, "t-01", stance, round_no=1)))
                code, out, err = cli(
                    ["finding", str(run), "--file", str(f), "--role", role], tmp)
                self.assertEqual(code, 0, err)
            code, out, err = cli(
                ["brief", str(run), "--round", "2", "--role", "researcher"], tmp)
            self.assertEqual(code, 0, err)
            packet = json.loads(Path(parse_json(out)["brief"]).read_text())
            lv = packet["ledger_view"]
            self.assertEqual(lv["positions"]["t-01"]["researcher"], "support")
            self.assertEqual(lv["positions"]["t-01"]["contrarian"], "refute")
            self.assertEqual(len(lv["open_refutes"]), 1)
            ref = lv["open_refutes"][0]
            self.assertEqual(ref["role"], "contrarian")
            self.assertEqual(ref["stance"], "refute")
            # the judge-whitelist shape: no quote_or_excerpt
            for ev in ref["evidence"]:
                self.assertEqual(set(ev), {"source", "claim"})
            self.assertNotIn("quote_or_excerpt", json.dumps(ref))
            self.assertIn("evidence", packet["task"])


class FindingTest(unittest.TestCase):
    def _run_with_problem(self, tmp):
        charter = write_charter(tmp)
        code, out, err = cli(["scaffold", str(charter)], tmp)
        self.assertEqual(code, 0, err)
        run = Path(parse_json(out)["run"])
        brief = Path(tmp) / "brief.txt"
        brief.write_text("Should we ship the beta by Friday or hold for next sprint?")
        code, out, err = cli(["record-brief", str(run), "--file", str(brief)], tmp)
        self.assertEqual(code, 0, err)
        return run

    def test_role_mismatch_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_with_problem(tmp)
            f = Path(tmp) / "f.json"
            # file says contrarian, but we pass --role researcher
            f.write_text(json.dumps(finding("contrarian", "t-01", "refute")))
            code, out, err = cli(
                ["finding", str(run), "--file", str(f), "--role", "researcher"], tmp)
            self.assertEqual(code, 1)
            self.assertIn("role mismatch", parse_json(out)["error"])
            self.assertEqual(
                [e for e in engine.read_events(run) if e["type"] == "finding"], [])

    def test_researcher_without_evidence_exits_4(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_with_problem(tmp)
            payload = finding("researcher", "t-01", "support")
            payload["evidence"] = []  # researcher: no citation, no finding
            f = Path(tmp) / "f.json"
            f.write_text(json.dumps(payload))
            code, out, err = cli(
                ["finding", str(run), "--file", str(f), "--role", "researcher"], tmp)
            self.assertEqual(code, 4)
            self.assertIn("error", parse_json(out))

    def test_non_member_role_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_with_problem(tmp)
            f = Path(tmp) / "f.json"
            f.write_text(json.dumps(finding("game-architect", "t-01", "support")))
            code, out, err = cli(
                ["finding", str(run), "--file", str(f),
                 "--role", "game-architect"], tmp)
            self.assertEqual(code, 1)
            self.assertIn("not a member", parse_json(out)["error"])

    def test_finding_appends_and_reports_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_with_problem(tmp)
            f = Path(tmp) / "f.json"
            f.write_text(json.dumps(finding("researcher", "t-01", "support")))
            code, out, err = cli(
                ["finding", str(run), "--file", str(f),
                 "--role", "researcher", "--model", "local-model"], tmp)
            self.assertEqual(code, 0, err)
            info = parse_json(out)
            self.assertEqual(info["finding"], "f-001")
            self.assertEqual(info["round"], 1)
            self.assertEqual(info["topic"], "t-01")
            ev = engine.read_events(run)[-1]
            self.assertEqual(ev["provenance"]["model"], "local-model")
            self.assertEqual(ev["provenance"]["role"], "researcher")

    def test_malformed_json_file_exits_4(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_with_problem(tmp)
            f = Path(tmp) / "f.json"
            f.write_text("{not json")
            code, out, err = cli(
                ["finding", str(run), "--file", str(f), "--role", "researcher"], tmp)
            self.assertEqual(code, 4)

    def test_charter_model_enforced_when_flag_passed(self):
        # Spec 2.2: provenance is charter-explicit. A charter that names a
        # model for a role records it in the ledger; passing the same
        # --model is consistent and allowed.
        with tempfile.TemporaryDirectory() as tmp:
            members = [dict(m) for m in CORE_CHARTER_MEMBERS]
            members[3]["model"] = "cloud-decorrelated"  # researcher
            charter = write_charter(tmp, members=members)
            code, out, err = cli(["scaffold", str(charter)], tmp)
            self.assertEqual(code, 0, err)
            run = Path(parse_json(out)["run"])
            f = Path(tmp) / "f.json"
            f.write_text(json.dumps(finding("researcher", "t-01", "support")))
            code, out, err = cli(
                ["finding", str(run), "--file", str(f),
                 "--role", "researcher", "--model", "cloud-decorrelated"], tmp)
            self.assertEqual(code, 0, err)
            ev = engine.read_events(run)[-1]
            self.assertEqual(ev["provenance"]["model"], "cloud-decorrelated")

    def test_charter_model_mismatch_exits_1(self):
        # Provenance is an engine fact, not a member claim: a --model that
        # contradicts the charter's declared model is refused.
        with tempfile.TemporaryDirectory() as tmp:
            members = [dict(m) for m in CORE_CHARTER_MEMBERS]
            members[3]["model"] = "charter-model"  # researcher
            charter = write_charter(tmp, members=members)
            code, out, err = cli(["scaffold", str(charter)], tmp)
            self.assertEqual(code, 0, err)
            run = Path(parse_json(out)["run"])
            f = Path(tmp) / "f.json"
            f.write_text(json.dumps(finding("researcher", "t-01", "support")))
            code, out, err = cli(
                ["finding", str(run), "--file", str(f),
                 "--role", "researcher", "--model", "something-else"], tmp)
            self.assertEqual(code, 1)
            self.assertIn("provenance mismatch", parse_json(out)["error"])
            self.assertEqual(
                [e for e in engine.read_events(run) if e["type"] == "finding"], [])

    def test_charter_model_recorded_without_flag(self):
        # No --model, charter declares one: the declared model is the
        # provenance (the orchestrator is not required to repeat it).
        with tempfile.TemporaryDirectory() as tmp:
            members = [dict(m) for m in CORE_CHARTER_MEMBERS]
            members[3]["model"] = "charter-model"  # researcher
            charter = write_charter(tmp, members=members)
            code, out, err = cli(["scaffold", str(charter)], tmp)
            self.assertEqual(code, 0, err)
            run = Path(parse_json(out)["run"])
            f = Path(tmp) / "f.json"
            f.write_text(json.dumps(finding("researcher", "t-01", "support")))
            code, out, err = cli(
                ["finding", str(run), "--file", str(f), "--role", "researcher"], tmp)
            self.assertEqual(code, 0, err)
            ev = engine.read_events(run)[-1]
            self.assertEqual(ev["provenance"]["model"], "charter-model")


class NoteRoundTest(unittest.TestCase):
    def test_note_round_counts_findings_in_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            charter = write_charter(tmp)
            code, out, err = cli(["scaffold", str(charter)], tmp)
            run = Path(parse_json(out)["run"])
            for i, (role, stance) in enumerate(
                    (("researcher", "support"), ("contrarian", "refute"))):
                f = Path(tmp) / f"f{i}.json"
                f.write_text(json.dumps(finding(role, "t-01", stance, round_no=1)))
                code, out, err = cli(
                    ["finding", str(run), "--file", str(f), "--role", role], tmp)
                self.assertEqual(code, 0, err)
            code, out, err = cli(["note-round", str(run), "--round", "1"], tmp)
            self.assertEqual(code, 0, err)
            self.assertEqual(parse_json(out), {"finding_count": 2, "round": 1})
            digests = [e for e in engine.read_events(run)
                       if e["type"] == "digest" and e["payload"]["kind"] == "round-end"]
            self.assertEqual(digests[-1]["payload"]["finding_count"], 2)
            self.assertEqual(digests[-1]["provenance"]["role"], "engine")


class CloseTest(unittest.TestCase):
    def _run_with_sealed_ruling(self, tmp):
        """A run with a contested topic, a blind brief, and a sealed ruling."""
        charter = write_charter(tmp)
        code, out, err = cli(["scaffold", str(charter)], tmp)
        self.assertEqual(code, 0, err)
        run = Path(parse_json(out)["run"])
        brief = Path(tmp) / "brief.txt"
        brief.write_text("Should we ship the beta by Friday or hold for next sprint?")
        code, out, err = cli(["record-brief", str(run), "--file", str(brief)], tmp)
        self.assertEqual(code, 0, err)
        # a contested topic: support + an evidenced, un-rebutted refute
        p1 = finding("researcher", "t-01", "support",
                     argument="The plan is sound on balance and ready to proceed now.",
                     evidence=[{"source": "reasoning", "claim": "the case supports it",
                                "quote_or_excerpt": "n/a"}])
        p2 = finding("contrarian", "t-01", "refute",
                     argument="The plan assumes a stable upstream that we do not control.",
                     evidence=[{"source": "reasoning", "claim": "the risk is real",
                                "quote_or_excerpt": "n/a"}])
        f1 = Path(tmp) / "f1.json"
        f1.write_text(json.dumps(p1))
        f2 = Path(tmp) / "f2.json"
        f2.write_text(json.dumps(p2))
        for f, role in ((f1, "researcher"), (f2, "contrarian")):
            code, out, err = cli(
                ["finding", str(run), "--file", str(f), "--role", role], tmp)
            self.assertEqual(code, 0, err)
        code, out, err = cli(["judge-brief", str(run)], tmp)
        self.assertEqual(code, 0, err)
        self.assertIn('"wall": "clean"', out)
        ruling_file = Path(tmp) / "ruling.json"
        ruling_file.write_text(json.dumps(ruling("t-01")))
        code, out, err = cli(
            ["seal-ruling", str(run), "--ruling-file", str(ruling_file)], tmp)
        self.assertEqual(code, 0, err)
        self.assertEqual(parse_json(out)["sealed"], ["r-001"])
        return run

    def test_seal_records_judge_model_provenance(self):
        # Spec 2.2: the judge is a role too; its ruling carries provenance.
        # Without --model, the charter's declared judge model is used (null
        # here = config default).
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_with_sealed_ruling(tmp)
            ruling_ev = next(e for e in engine.read_events(run)
                             if e["type"] == "ruling")
            self.assertEqual(ruling_ev["provenance"]["role"], "judge")
            self.assertIsNone(ruling_ev["provenance"]["model"])

    def test_seal_records_explicit_judge_model(self):
        # A charter-explicit judge model flows into the ruling's provenance
        # without the orchestrator having to pass --model (the engine reads
        # the charter), matching the finding behavior.
        with tempfile.TemporaryDirectory() as tmp:
            charter2 = write_charter(
                tmp,
                members=[dict(m, model=("deepseek-judge" if m["role"] == "judge" else None))
                         for m in CORE_CHARTER_MEMBERS])
            code, out, err = cli(["scaffold", str(charter2)], tmp)
            self.assertEqual(code, 0, err)
            run2 = Path(parse_json(out)["run"])
            brief = Path(tmp) / "b2.txt"
            brief.write_text("Should we ship the beta by Friday or hold for next sprint?")
            code, out, err = cli(["record-brief", str(run2), "--file", str(brief)], tmp)
            self.assertEqual(code, 0, err)
            f1 = Path(tmp) / "g1.json"
            f1.write_text(json.dumps(finding("researcher", "t-01", "support",
                                             argument="The plan is sound on balance and ready to proceed now.",
                                             evidence=[{"source": "reasoning", "claim": "the case supports it",
                                                        "quote_or_excerpt": "n/a"}])))
            f2 = Path(tmp) / "g2.json"
            f2.write_text(json.dumps(finding("contrarian", "t-01", "refute",
                                             argument="The plan assumes a stable upstream that we do not control.",
                                             evidence=[{"source": "reasoning", "claim": "the risk is real",
                                                        "quote_or_excerpt": "n/a"}])))
            for f, role in ((f1, "researcher"), (f2, "contrarian")):
                code, out, err = cli(["finding", str(run2), "--file", str(f), "--role", role], tmp)
                self.assertEqual(code, 0, err)
            code, out, err = cli(["judge-brief", str(run2)], tmp)
            self.assertEqual(code, 0, err)
            rf = Path(tmp) / "r.json"
            rf.write_text(json.dumps(ruling("t-01")))
            code, out, err = cli(["seal-ruling", str(run2), "--ruling-file", str(rf)], tmp)
            self.assertEqual(code, 0, err)
            ruling_ev = next(e for e in engine.read_events(run2)
                             if e["type"] == "ruling")
            self.assertEqual(ruling_ev["provenance"]["model"], "deepseek-judge")

    def test_seal_judge_model_mismatch_exits_1(self):
        # A --model contradicting the charter's declared judge model is
        # refused (provenance is an engine fact, not a member claim).
        with tempfile.TemporaryDirectory() as tmp:
            charter2 = write_charter(
                tmp,
                members=[dict(m, model=("charter-judge" if m["role"] == "judge" else None))
                         for m in CORE_CHARTER_MEMBERS])
            code, out, err = cli(["scaffold", str(charter2)], tmp)
            self.assertEqual(code, 0, err)
            run2 = Path(parse_json(out)["run"])
            brief = Path(tmp) / "b3.txt"
            brief.write_text("Should we ship the beta by Friday or hold for next sprint?")
            code, out, err = cli(["record-brief", str(run2), "--file", str(brief)], tmp)
            self.assertEqual(code, 0, err)
            f1 = Path(tmp) / "h1.json"
            f1.write_text(json.dumps(finding("researcher", "t-01", "support",
                                             argument="The plan is sound on balance and ready to proceed now.",
                                             evidence=[{"source": "reasoning", "claim": "the case supports it",
                                                        "quote_or_excerpt": "n/a"}])))
            f2 = Path(tmp) / "h2.json"
            f2.write_text(json.dumps(finding("contrarian", "t-01", "refute",
                                             argument="The plan assumes a stable upstream that we do not control.",
                                             evidence=[{"source": "reasoning", "claim": "the risk is real",
                                                        "quote_or_excerpt": "n/a"}])))
            for f, role in ((f1, "researcher"), (f2, "contrarian")):
                code, out, err = cli(["finding", str(run2), "--file", str(f), "--role", role], tmp)
                self.assertEqual(code, 0, err)
            code, out, err = cli(["judge-brief", str(run2)], tmp)
            self.assertEqual(code, 0, err)
            rf = Path(tmp) / "r3.json"
            rf.write_text(json.dumps(ruling("t-01")))
            code, out, err = cli(["seal-ruling", str(run2), "--ruling-file", str(rf),
                                  "--model", "something-else"], tmp)
            self.assertEqual(code, 1)
            self.assertIn("provenance mismatch", parse_json(out)["error"])
            self.assertEqual(
                [e for e in engine.read_events(run2) if e["type"] == "ruling"], [])

    def test_report_judge_chartered_not_invoked(self):
        # No impasse, no ruling: the report still shows the chartered judge
        # model (marked not invoked) so the heterogeneity budget reflects the
        # chartered design, not just what fired.
        with tempfile.TemporaryDirectory() as tmp:
            charter = write_charter(
                tmp,
                members=[dict(m, model=("deepseek-judge" if m["role"] == "judge" else None))
                         for m in CORE_CHARTER_MEMBERS])
            code, out, err = cli(["scaffold", str(charter)], tmp)
            self.assertEqual(code, 0, err)
            run = Path(parse_json(out)["run"])
            brief = Path(tmp) / "b4.txt"
            brief.write_text("Should we ship the beta by Friday or hold for next sprint?")
            code, out, err = cli(["record-brief", str(run), "--file", str(brief)], tmp)
            self.assertEqual(code, 0, err)
            f1 = Path(tmp) / "i1.json"
            f1.write_text(json.dumps(finding("researcher", "t-01", "support",
                                             argument="The plan is sound on balance and ready to proceed now.",
                                             evidence=[{"source": "reasoning", "claim": "the case supports it",
                                                        "quote_or_excerpt": "n/a"}])))
            f2 = Path(tmp) / "i2.json"
            f2.write_text(json.dumps(finding("librarian", "t-01", "support",
                                             argument="The record and the charter both point the same way here.",
                                             evidence=[{"source": "reasoning", "claim": "the framing supports it",
                                                        "quote_or_excerpt": "n/a"}])))
            for f, role in ((f1, "researcher"), (f2, "librarian")):
                code, out, err = cli(["finding", str(run), "--file", str(f),
                                      "--role", role], tmp)
                self.assertEqual(code, 0, err)
            code, out, err = cli(["note-round", str(run), "--round", "1"], tmp)
            self.assertEqual(code, 0, err)
            code, out, err = cli(["check", str(run)], tmp)
            self.assertEqual(parse_json(out)["action"], "recommend")
            rec = Path(tmp) / "rec2.json"
            rec.write_text(json.dumps({
                "recommendation": "Adopt the supported position on t-01.",
                "rationale": "t-01 resolved by support with no un-rebutted refutes.",
                "resolved": [{"topic": "t-01", "outcome": "resolved in round 1"}],
                "rulings_applied": [], "dissent": [], "confidence": 0.9}))
            code, out, err = cli(["close", str(run), "--recommendation-file", str(rec)], tmp)
            self.assertEqual(code, 0, err)
            report = (run / "report.md").read_text(encoding="utf-8")
            self.assertIn("- judge: deepseek-judge (chartered; not invoked - no impasse)",
                          report)
            self.assertIn("decorrelation: heterogeneous", report)

    def test_close_report_has_heterogeneity_budget(self):
        # Spec 2.2: report.md states which model produced the findings and
        # the judge, and flags single-model runs as untested decorrelation.
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_with_sealed_ruling(tmp)
            rec = Path(tmp) / "rec.json"
            rec.write_text(json.dumps(self._recommendation(["r-001"])))
            code, out, err = cli(
                ["close", str(run), "--recommendation-file", str(rec)], tmp)
            self.assertEqual(code, 0, err)
            report = (run / "report.md").read_text(encoding="utf-8")
            self.assertIn("## Heterogeneity budget", report)
            self.assertIn("- judge: config default (no charter override)", report)
            self.assertIn("- config default: contrarian, researcher", report)
            self.assertIn("decorrelation: single model", report)

    def test_seal_stamps_sealed_at_overriding_judge_placeholder(self):
        # Regression: the dogfood judge supplied a placeholder sealed_at it
        # cannot know; the engine must stamp the seal time, not preserve the
        # placeholder (design law 3: sealed_at is an engine fact).
        with tempfile.TemporaryDirectory() as tmp:
            charter = write_charter(tmp)
            code, out, err = cli(["scaffold", str(charter)], tmp)
            self.assertEqual(code, 0, err)
            run = Path(parse_json(out)["run"])
            brief = Path(tmp) / "brief.txt"
            brief.write_text("Ship the beta by Friday or hold for next sprint?")
            code, out, err = cli(
                ["record-brief", str(run), "--file", str(brief)], tmp)
            self.assertEqual(code, 0, err)
            p1 = finding("researcher", "t-01", "support",
                         argument="The plan is sound and ready to proceed now.",
                         evidence=[{"source": "reasoning",
                                     "claim": "the case supports it",
                                     "quote_or_excerpt": "n/a"}])
            p2 = finding("contrarian", "t-01", "refute",
                         argument="The plan assumes a stable upstream we lack.",
                         evidence=[{"source": "reasoning",
                                     "claim": "the risk is real",
                                     "quote_or_excerpt": "n/a"}])
            for i, (f, role) in enumerate(((p1, "researcher"),
                                           (p2, "contrarian"))):
                fp = Path(tmp) / f"f{i}.json"
                fp.write_text(json.dumps(f))
                code, out, err = cli(
                    ["finding", str(run), "--file", str(fp), "--role", role], tmp)
                self.assertEqual(code, 0, err)
            code, out, err = cli(["note-round", str(run), "--round", "1"], tmp)
            self.assertEqual(code, 0, err)
            code, out, err = cli(["judge-brief", str(run)], tmp)
            self.assertEqual(code, 0, err)
            placeholder = "2000-01-01T00:00:00Z"
            rl = ruling("t-01")
            rl["sealed_at"] = placeholder  # judge cannot know the seal time
            ruling_file = Path(tmp) / "ruling.json"
            ruling_file.write_text(json.dumps(rl))
            code, out, err = cli(
                ["seal-ruling", str(run), "--ruling-file", str(ruling_file)], tmp)
            self.assertEqual(code, 0, err)
            rulings = [e for e in engine.read_events(run) if e["type"] == "ruling"]
            self.assertEqual(len(rulings), 1)
            sealed = rulings[0]["payload"]["sealed_at"]
            self.assertNotEqual(sealed, placeholder)
            self.assertNotIn("2000-01-01", sealed)

    @staticmethod
    def _recommendation(rulings_applied):
        return {
            "recommendation": "Adopt the supported position on t-01 within the ruling.",
            "rationale": "The sealed ruling on t-01 is binding and support holds.",
            "resolved": [{"topic": "t-01", "outcome": "resolved by ruling"}],
            "rulings_applied": rulings_applied,
            "dissent": [{"role": "contrarian", "topic": "t-01",
                         "position": "The upstream risk was underweighted."}],
            "confidence": 0.7,
        }

    def test_close_missing_sealed_ruling_id_exits_1_and_names_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_with_sealed_ruling(tmp)
            rec = Path(tmp) / "rec.json"
            rec.write_text(json.dumps(self._recommendation([])))  # omits r-001
            code, out, err = cli(
                ["close", str(run), "--recommendation-file", str(rec)], tmp)
            self.assertEqual(code, 1)
            msg = parse_json(out)["error"]
            self.assertIn("r-001", msg)
            # nothing appended
            self.assertEqual(
                [e for e in engine.read_events(run) if e["type"] == "close"], [])

    def test_close_success_appends_events_and_writes_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_with_sealed_ruling(tmp)
            rec = Path(tmp) / "rec.json"
            rec.write_text(json.dumps(self._recommendation(["r-001"])))
            code, out, err = cli(
                ["close", str(run), "--recommendation-file", str(rec)], tmp)
            self.assertEqual(code, 0, err)
            info = parse_json(out)
            self.assertEqual(info, {"closed": True, "run": str(run.resolve()),
                                    "rulings": 1})
            types = [e["type"] for e in engine.read_events(run)]
            self.assertEqual(types[-2:], ["recommendation", "close"])
            close_ev = engine.read_events(run)[-1]["payload"]
            self.assertEqual(close_ev["status"], "closed")
            self.assertEqual(close_ev["topics"], ["t-01"])
            self.assertEqual(close_ev["ruling_count"], 1)
            self.assertTrue((run / "recommendation.md").is_file())
            self.assertTrue((run / "report.md").is_file())
            self.assertIn("Adopt the supported position",
                          (run / "recommendation.md").read_text())

    def test_close_twice_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_with_sealed_ruling(tmp)
            rec = Path(tmp) / "rec.json"
            rec.write_text(json.dumps(self._recommendation(["r-001"])))
            code, _, _ = cli(["close", str(run), "--recommendation-file", str(rec)], tmp)
            self.assertEqual(code, 0)
            code, out, err = cli(
                ["close", str(run), "--recommendation-file", str(rec)], tmp)
            self.assertEqual(code, 1)
            self.assertIn("already closed", parse_json(out)["error"])

    def test_close_invalid_recommendation_exits_4(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._run_with_sealed_ruling(tmp)
            rec = Path(tmp) / "rec.json"
            bad = self._recommendation(["r-001"])
            del bad["recommendation"]  # required field
            rec.write_text(json.dumps(bad))
            code, out, err = cli(
                ["close", str(run), "--recommendation-file", str(rec)], tmp)
            self.assertEqual(code, 4)


class EndToEndTest(unittest.TestCase):
    def test_scripted_run_scaffold_to_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            # scaffold (no problem file) -> record-brief
            charter = write_charter(tmp)
            code, out, err = cli(["scaffold", str(charter)], tmp)
            self.assertEqual(code, 0, err)
            run = Path(parse_json(out)["run"])
            self.assertEqual(parse_json(out)["events"], 1)

            brief = Path(tmp) / "brief.txt"
            brief.write_text("Should we ship the beta by Friday or hold for next sprint?")
            code, out, err = cli(["record-brief", str(run), "--file", str(brief)], tmp)
            self.assertEqual(code, 0, err)

            # add-source
            src = Path(tmp) / "src.txt"
            src.write_text("Release notes: the beta build is green on CI.")
            code, out, err = cli(["add-source", str(run), "--file", str(src)], tmp)
            self.assertEqual(code, 0, err)

            # brief for round 1
            code, out, err = cli(
                ["brief", str(run), "--round", "1", "--role", "researcher"], tmp)
            self.assertEqual(code, 0, err)

            # findings round 1 (t-01 contested)
            r1 = [
                ("researcher", "t-01", "support"),
                ("contrarian", "t-01", "refute"),
                ("librarian", "t-01", "support"),
            ]
            for i, (role, topic, stance) in enumerate(r1):
                f = Path(tmp) / f"f1-{i}.json"
                f.write_text(json.dumps(finding(role, topic, stance, round_no=1)))
                code, out, err = cli(
                    ["finding", str(run), "--file", str(f), "--role", role], tmp)
                self.assertEqual(code, 0, err)

            # note-round 1 + check
            code, out, err = cli(["note-round", str(run), "--round", "1"], tmp)
            self.assertEqual(code, 0, err)
            self.assertEqual(parse_json(out)["finding_count"], 3)
            code, out, err = cli(["check", str(run)], tmp)
            self.assertEqual(code, 0, err)
            chk = parse_json(out)
            self.assertIn("t-01", chk["contested_topics"])
            self.assertEqual(chk["action"], "continue")

            # findings round 2 (a new topic)
            f2 = Path(tmp) / "f2-0.json"
            f2.write_text(json.dumps(finding("researcher", "t-02", "support",
                                             round_no=2)))
            code, out, err = cli(
                ["finding", str(run), "--file", str(f2),
                 "--role", "researcher"], tmp)
            self.assertEqual(code, 0, err)

            # note-round 2 + check
            code, out, err = cli(["note-round", str(run), "--round", "2"], tmp)
            self.assertEqual(code, 0, err)
            self.assertEqual(parse_json(out)["finding_count"], 1)
            code, out, err = cli(["check", str(run)], tmp)
            self.assertEqual(code, 0, err)

            # verify: the whole chain is intact
            code, out, err = cli(["verify", str(run)], tmp)
            self.assertEqual(code, 0, err)
            self.assertEqual(parse_json(out)["chain"], "ok")

    def test_run_dir_not_found_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = cli(["check", str(Path(tmp) / "nope")], tmp)
            self.assertEqual(code, 1)
            self.assertIn("not found", parse_json(out)["error"])


if __name__ == "__main__":
    unittest.main()
