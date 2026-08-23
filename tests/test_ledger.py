"""Unit tests for the ledger: append, id assignment, verify, tamper detection."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _engine import (
    engine,
    finding,
    make_run,
    run_dir_for_subprocess,
    ruling,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
ENGINE_PATH = REPO_ROOT / "scripts" / "council.py"


def rewrite_ledger(run, mutate):
    """Rewrite ledger.jsonl: parse every line, apply mutate(lines), re-serialize."""
    path = engine.ledger_path(run)
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    mutate(lines)
    path.write_text(
        "".join(engine.canonical(ev) + "\n" for ev in lines), encoding="utf-8"
    )


class AppendEventTest(unittest.TestCase):
    def test_seq_is_sequential_from_one_and_genesis_hash_is_zeros(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            ev = engine.append_event(run, "digest", {"round": 1, "kind": "round-end"},
                                     role="engine")
            self.assertEqual(ev["seq"], 2)
            events = engine.read_events(run)
            self.assertEqual([e["seq"] for e in events], [1, 2])
            self.assertEqual(events[0]["prev_hash"], "0" * 64)
            for prev, ev in zip(events, events[1:]):
                self.assertEqual(ev["prev_hash"], engine.sha256_hex(engine.canonical(prev)))

    def test_finding_and_ruling_ids_autoassigned_monotonic_never_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            f1 = engine.append_event(run, "finding",
                                     finding("researcher", "t-01", "support"),
                                     role="researcher")
            f2 = engine.append_event(run, "finding",
                                     finding("contrarian", "t-01", "refute"),
                                     role="contrarian")
            r1 = engine.append_event(run, "ruling", ruling("t-01"), role="judge")
            f3 = engine.append_event(run, "finding",
                                     finding("librarian", "t-02", "support"),
                                     role="librarian")
            r2 = engine.append_event(run, "ruling", ruling("t-02"), role="judge")
            self.assertEqual(f1["payload"]["id"], "f-001")
            self.assertEqual(f2["payload"]["id"], "f-002")
            self.assertEqual(f3["payload"]["id"], "f-003")
            self.assertEqual(r1["payload"]["id"], "r-001")
            self.assertEqual(r2["payload"]["id"], "r-002")

    def test_refuses_finding_missing_evidence_exit_4(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            payload = finding("researcher", "t-01", "support")
            del payload["evidence"]
            with self.assertRaises(SystemExit) as cm:
                engine.append_event(run, "finding", payload, role="researcher")
            self.assertEqual(cm.exception.code, 4)
            self.assertEqual(len(engine.read_events(run)), 1)  # charter only

    def test_refuses_finding_unknown_extra_field_exit_4(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            payload = finding("researcher", "t-01", "support")
            payload["hotfix"] = "not in the schema"
            with self.assertRaises(SystemExit) as cm:
                engine.append_event(run, "finding", payload, role="researcher")
            self.assertEqual(cm.exception.code, 4)
            self.assertEqual(len(engine.read_events(run)), 1)

    def test_refuses_ruling_binding_false_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            payload = ruling("t-01")
            payload["binding"] = False
            with self.assertRaises(SystemExit) as cm:
                engine.append_event(run, "ruling", payload, role="judge")
            self.assertEqual(cm.exception.code, 1)
            self.assertEqual(len(engine.read_events(run)), 1)  # charter only


class VerifyTest(unittest.TestCase):
    def _three_finding_chain(self, run):
        engine.append_event(run, "finding",
                            finding("researcher", "t-01", "support"),
                            role="researcher")
        engine.append_event(run, "finding",
                            finding("contrarian", "t-01", "refute"),
                            role="contrarian")
        engine.append_event(run, "finding",
                            finding("librarian", "t-01", "support",
                                    rebutting=["f-002"]),
                            role="librarian")

    def _assert_exit(self, run, code):
        with self.assertRaises(SystemExit) as cm:
            engine.verify(run)
        self.assertEqual(cm.exception.code, code)

    def test_verify_clean_chain_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            self._three_finding_chain(run)
            events = engine.verify(run)
            self.assertEqual(len(events), 4)

    def test_tampered_argument_byte_breaks_chain_exit_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            self._three_finding_chain(run)
            def flip_middle_argument(lines):
                lines[2]["payload"]["argument"] = (
                    lines[2]["payload"]["argument"].replace("sufficiently", "sufficiently!")
                )
            rewrite_ledger(run, flip_middle_argument)
            self._assert_exit(run, 3)

    def test_deleted_middle_line_breaks_chain_exit_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            self._three_finding_chain(run)
            rewrite_ledger(run, lambda lines: lines.pop(2))
            self._assert_exit(run, 3)

    def test_reordered_lines_break_chain_exit_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            self._three_finding_chain(run)
            def swap(lines):
                lines[1], lines[2] = lines[2], lines[1]
            rewrite_ledger(run, swap)
            self._assert_exit(run, 3)

    def test_seq_gap_breaks_chain_exit_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            self._three_finding_chain(run)
            rewrite_ledger(run, lambda lines: lines[2].update(seq=99))
            self._assert_exit(run, 3)

    def test_handwritten_schema_invalid_finding_in_valid_chain_exit_3(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            engine.append_event(run, "finding",
                                finding("researcher", "t-01", "support"),
                                role="researcher")
            last = engine.read_events(run)[-1]
            bad = {
                "seq": last["seq"] + 1,
                "ts": last["ts"],
                "type": "finding",
                "payload": {
                    "id": "f-999",
                    "round": 1,
                    "role": "researcher",
                    "topic": "t-01",
                    "stance": "agree",  # not in enum: support | refute
                    "argument": "Hand-written line that bypasses append_event.",
                    "evidence": [{"source": "reasoning", "claim": "x",
                                  "quote_or_excerpt": "n/a"}],
                    "confidence": 0.5,
                },
                "provenance": {"role": "engine", "model": None},
                "prev_hash": engine.sha256_hex(engine.canonical(last)),
            }
            with open(engine.ledger_path(run), "a") as f:
                f.write(engine.canonical(bad) + "\n")
            self._assert_exit(run, 3)


class ConcurrencyTest(unittest.TestCase):
    WORKER = r'''
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("council_engine", sys.argv[1])
eng = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eng)

run, role, n = sys.argv[2], sys.argv[3], int(sys.argv[4])
payload = {
    "round": 1,
    "role": role,
    "topic": "t-01",
    "stance": "support",
    "argument": f"Concurrent append by {role}; a long enough argument line.",
    "evidence": [{"source": "reasoning", "claim": f"{role} appended",
                  "quote_or_excerpt": "n/a"}],
    "confidence": 0.5,
}
for _ in range(n):
    eng.append_event(run, "finding", dict(payload), role=role)
'''

    def test_two_processes_appending_concurrently_produce_valid_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = run_dir_for_subprocess(tmp)
            worker = Path(tmp) / "worker.py"
            worker.write_text(self.WORKER, encoding="utf-8")
            n = 5
            workers = [
                subprocess.Popen(
                    [sys.executable, str(worker), str(ENGINE_PATH), str(run),
                     f"member{role}", str(n)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                )
                for role in "ab"
            ]
            for w in workers:
                out, err = w.communicate()
                self.assertEqual(w.returncode, 0, err.decode())
            events = engine.read_events(run)
            self.assertEqual(len(events), 2 * n)
            self.assertEqual([e["seq"] for e in events], list(range(1, 2 * n + 1)))
            self.assertEqual(engine.verify(run), events)


if __name__ == "__main__":
    unittest.main()
