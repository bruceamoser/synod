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

from _engine import engine, finding, ruling, write_charter

ALL_COMMANDS = [
    "scaffold", "record-brief", "add-source", "finding", "note-round",
    "brief", "verify", "check", "judge-brief", "seal-ruling", "close",
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
