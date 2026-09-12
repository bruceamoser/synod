"""A finding's topic must be a t-NN id (or the literal 'problem-scoping').

Why this is load-bearing: the ruling and recommendation schemas link topics by id, and the
blind judge can only rule on a t-NN topic. A descriptive topic name is accepted by every
earlier stage (scaffold, brief, ingest, check) and only fails at SEALING, after the whole
council has been dispatched. That is the worst possible place to discover it, so the finding
schema refuses it at ingest instead.
"""
import json
import pathlib
import unittest

SCHEMA = json.loads(
    (pathlib.Path(__file__).resolve().parents[1] / "references/schemas/finding.schema.json").read_text()
)
PATTERN = SCHEMA["properties"]["topic"]["pattern"]


def matches(topic: str) -> bool:
    import re
    return re.match(PATTERN, topic) is not None


class TestFindingTopicPattern(unittest.TestCase):
    def test_t_nn_topics_accepted(self):
        for t in ("t-01", "t-02", "t-10", "t-123"):
            self.assertTrue(matches(t), f"{t} should be accepted")

    def test_problem_scoping_accepted(self):
        self.assertTrue(matches("problem-scoping"))

    def test_descriptive_topics_refused(self):
        for t in ("damage-budget-adept-base-row", "glossary-term-collision",
                  "spell-card-page-integrity", "t-1", "topic", ""):
            self.assertFalse(matches(t), f"{t!r} should be refused at ingest, not at sealing")
