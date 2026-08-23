"""Unit tests for the blind wall: n-gram lint, leak refusal, clean brief, whitelist."""
import contextlib
import io
import json
import tempfile
import unittest

from _engine import engine, finding, make_run

# a verbatim span of 12 words (>= NGRAM_N): the leak material
LEAK = ("the quarterly settlement window closes before the fiscal year "
        "end audit begins")
TEN = "alpha beta gamma gamma2 delta epsilon zeta eta theta iota"
NINE = TEN.split()[:9]

WHITELISTED_KEYS = {
    "id", "round", "role", "stance", "argument", "confidence",
    "rebutting", "evidence",
}
WHITELISTED_EVIDENCE_KEYS = {"source", "claim"}


def leak_argument():
    # >= 20 chars, embeds the forbidden span verbatim
    return ("We agree on the point: " + LEAK + " and that is precisely "
            "why we concur with the motion.")


class NgramTest(unittest.TestCase):
    def test_ngrams_are_10_word_lowercase_punctuation_stripped_spans(self):
        text = "Hello, World! This is a test of the ngram function. Right?"
        grams = engine.ngrams(text)
        self.assertEqual(
            grams,
            {
                "hello world this is a test of the ngram function",
                "world this is a test of the ngram function right",
            },
        )
        for g in grams:
            self.assertEqual(g, g.lower())
            for punct in (",", "!", ".", "?", '"', "'", "(", ")"):
                self.assertNotIn(punct, g)

    def test_shared_nine_word_span_does_not_trigger(self):
        corpus = "xx " + " ".join(NINE) + " yy"
        brief = "aa " + " ".join(NINE) + " bb"
        self.assertEqual(engine.wall_lint(brief, {"doc": corpus}), {})

    def test_shared_ten_word_span_triggers(self):
        corpus = "xx " + TEN + " yy"
        brief = "aa " + TEN + " bb"
        hits = engine.wall_lint(brief, {"doc": corpus})
        self.assertEqual(hits, {"doc": [TEN]})


class LeakDetectionTest(unittest.TestCase):
    def _leak_run(self, tmp):
        problem = ("The widget pipeline decision. " + LEAK +
                   " The council must choose a lane.")
        sources = {"source-a.txt": "Exhibit A. " + LEAK +
                   " End of excerpt."}
        run = make_run(tmp, problem_text=problem, sources=sources)
        engine.append_event(
            run, "finding",
            finding("researcher", "t-01", "support", argument=leak_argument()),
            role="researcher",
        )
        return run

    def test_verbatim_span_from_source_refuses_brief_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._leak_run(tmp)
            with self.assertRaises(SystemExit) as cm:
                engine.judge_brief(run)
            self.assertEqual(cm.exception.code, 2)
            self.assertFalse((run / "judge" / "brief.json").exists())
            rejected = run / "judge" / "brief.rejected.json"
            self.assertTrue(rejected.exists())
            diag = json.loads(rejected.read_text())
            hits = diag["corpus_hits"]
            # the leak was seen against the raw source AND the problem corpus
            self.assertIn("sources/source-a.txt", hits)
            self.assertIn("problem.md", hits)
            self.assertIn("problem-statement", hits)
            shared = [g for name in hits for g in hits[name]]
            self.assertTrue(any(LEAK.split()[:10] == g.split() for g in shared),
                            shared)

    def test_clean_paraphrase_passes_the_wall_exit_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            problem = ("The widget pipeline decision. " + LEAK +
                       " The council must choose a lane.")
            sources = {"source-a.txt": "Exhibit A. " + LEAK +
                       " End of excerpt."}
            run = make_run(tmp, problem_text=problem, sources=sources)
            paraphrase = ("Payments settle in one quarterly batch ahead of "
                          "the yearly review, so the auditors can start "
                          "their pass without waiting on anyone.")
            engine.append_event(
                run, "finding",
                finding("researcher", "t-01", "support", argument=paraphrase,
                        evidence=[{"source": "ledger-ref",
                                   "claim": "the batch settles ahead of review",
                                   "quote_or_excerpt": "see f-001"}]),
                role="researcher",
            )
            engine.append_event(
                run, "finding",
                finding("contrarian", "t-01", "refute",
                        argument=("The batch plan assumes perfect upstream "
                                  "feeds; one missed feed stalls the entire "
                                  "settlement cycle and reopens the window.")),
                role="contrarian",
            )
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                brief = engine.judge_brief(run)
            self.assertTrue((run / "judge" / "brief.json").exists())
            self.assertFalse((run / "judge" / "brief.rejected.json").exists())
            self.assertIn('"wall": "clean"', buf.getvalue())
            self.assertEqual(brief["wall"],
                             {"ngram_n": engine.NGRAM_N,
                              "reject_at": engine.NGRAM_REJECT_AT})
            self.assertEqual(sorted(brief["contested_topics"]), ["t-01"])

    def test_no_contested_topics_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            for role in ("librarian", "researcher"):
                engine.append_event(
                    run, "finding",
                    finding(role, "t-01", "support"), role=role,
                )
            with self.assertRaises(SystemExit) as cm:
                engine.judge_brief(run)
            self.assertEqual(cm.exception.code, 1)
            self.assertFalse((run / "judge" / "brief.json").exists())
            self.assertFalse((run / "judge" / "brief.rejected.json").exists())


class BriefWhitelistTest(unittest.TestCase):
    def test_brief_findings_carry_exactly_the_whitelisted_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            engine.append_event(
                run, "finding",
                finding("researcher", "t-01", "support",
                        evidence=[{"source": "ledger-ref", "claim": "cited",
                                   "quote_or_excerpt": "SECRET EXCERPT"}]),
                role="researcher",
            )
            engine.append_event(
                run, "finding",
                finding("contrarian", "t-01", "refute"),
                role="contrarian",
            )
            brief = engine.judge_brief(run)
            topic = brief["contested_topics"]["t-01"]
            for f in topic["findings"]:
                self.assertEqual(set(f), WHITELISTED_KEYS)
                for ev in f["evidence"]:
                    self.assertEqual(set(ev), WHITELISTED_EVIDENCE_KEYS)
            # the verbatim excerpt must not survive anywhere in the brief
            self.assertNotIn("SECRET EXCERPT", json.dumps(brief))
            self.assertNotIn("quote_or_excerpt", json.dumps(brief))
            # provenance (model/role-of-production) is not a brief field
            self.assertNotIn("provenance", json.dumps(brief))
            # the raw problem statement must not be in the brief either
            self.assertNotIn("Default problem statement", json.dumps(brief))


if __name__ == "__main__":
    unittest.main()
