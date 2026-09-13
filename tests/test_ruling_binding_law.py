"""Regression: the binding law is an ENGINE fact, not judge knowledge.

A judge writes its ruling file from the task schema it was given, which cannot
mention design law 3. Requiring the judge to assert `binding: true` therefore
made a real run unsealable *after* the whole council had run - twice, on ch12
and again on ch13.

The old test helper (`tests/_engine.py::ruling`) hard-coded `"binding": True`,
so the suite asserted the law on the judge's behalf and never exercised the
path a real judge takes. These tests write the ruling exactly as a judge does.

`sealed_at` set the precedent: the engine already stamps it because it is an
engine fact. `binding` now follows the same rule. An EXPLICIT false is still
refused, because the guard's purpose is to stop a caller filing a non-binding
ruling, and that purpose survives.
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _engine import engine, finding, write_charter


def cli(argv, root):
    out, err = io.StringIO(), io.StringIO()
    with patch.object(engine, "COUNCILS_ROOT", Path(root)), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = engine.main(list(argv))
        except SystemExit as e:
            code = e.code
    return (code if isinstance(code, int) else 1), out.getvalue(), err.getvalue()


def judge_ruling_file(tmp, topic="t-01", **overrides):
    """A ruling exactly as a judge writes one: no id, no sealed_at, and
    no `binding` unless the caller explicitly adds it."""
    payload = {
        "topic": topic,
        "point_of_contention": "Which position on the topic must stand?",
        "ruling": "The council must treat the defect as real and outstanding.",
        "reasoning": "The refute was specific, checkable, and never rebutted.",
        "conditions": ["Re-verify in a rebuilt PDF."],
    }
    payload.update(overrides)
    path = Path(tmp) / "judge-ruling.json"
    path.write_text(json.dumps(payload))
    return path


class BindingLawTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp = self.tmpdir.name
        charter = write_charter(tmp)
        code, out, err = cli(["scaffold", str(charter)], tmp)
        assert code == 0, err
        self.tmp = tmp
        self.council_run = Path(json.loads(out)["run"])

        brief = Path(tmp) / "brief.txt"
        brief.write_text("Is the chapter publishable as written?")
        cli(["record-brief", str(self.council_run), "--file", str(brief)], tmp)

        # one support, one refute -> a contested topic the judge must resolve
        for i, (role, stance) in enumerate((("researcher", "support"),
                                            ("contrarian", "refute"))):
            p = finding(role, "t-01", stance,
                        argument="The chapter holds up under its own conventions.",
                        evidence=[{"source": "reasoning",
                                   "claim": "checked against the source",
                                   "quote_or_excerpt": "n/a"}])
            fp = Path(tmp) / f"f{i}.json"
            fp.write_text(json.dumps(p))
            code, out, err = cli(["finding", str(self.council_run), "--file", str(fp),
                                  "--role", role], tmp)
            assert code == 0, err
        cli(["note-round", str(self.council_run), "--round", "1"], tmp)
        code, out, err = cli(["judge-brief", str(self.council_run)], tmp)
        assert code == 0, err

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_judge_ruling_without_binding_key_seals(self):
        """THE regression: a judge's file carries no `binding` key."""
        rf = judge_ruling_file(self.tmp)
        payload = json.loads(rf.read_text())
        self.assertNotIn("binding", payload, "guard against the test fixing itself")

        code, out, err = cli(["seal-ruling", str(self.council_run),
                              "--ruling-file", str(rf)], self.tmp)
        self.assertEqual(code, 0, f"seal refused a real judge file: {out}{err}")

        rulings = [e for e in engine.read_events(self.council_run)
                   if e["type"] == "ruling"]
        self.assertEqual(len(rulings), 1)
        stamped = rulings[0]["payload"]
        self.assertIs(stamped["binding"], True)
        self.assertTrue(stamped.get("sealed_at"))

    def test_explicit_false_binding_is_still_refused(self):
        """The guard's purpose survives: a caller cannot file a
        non-binding ruling."""
        rf = judge_ruling_file(self.tmp, binding=False)
        code, out, err = cli(["seal-ruling", str(self.council_run),
                              "--ruling-file", str(rf)], self.tmp)
        self.assertEqual(code, 1)
        self.assertIn("binding", out.lower())


if __name__ == "__main__":
    unittest.main()
