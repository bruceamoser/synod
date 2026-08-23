"""Unit tests for consensus math: quorum, topic states, rebuttal, freeze, impasse."""
import tempfile
import unittest

from _engine import (
    CORE_CHARTER_MEMBERS,
    charter_payload,
    engine,
    finding,
    make_run,
    ruling,
)

VOTERS = ("librarian", "contrarian", "researcher")


def append_finding(run, role, topic, stance, round_no=1, **kw):
    return engine.append_event(run, "finding", finding(role, topic, stance,
                                                       round_no, **kw),
                               role=role)


def append_round_end(run, round_no):
    return engine.append_event(run, "digest",
                               {"round": round_no, "kind": "round-end"},
                               role="engine")


class QuorumTest(unittest.TestCase):
    def test_default_quorum_is_strict_majority_of_voting_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)  # core 4 -> 3 voters
            r = engine.check(run)
            self.assertEqual(r["voting_members"], list(VOTERS))
            self.assertEqual(r["quorum"], 2)  # strict majority of 3

    def test_default_quorum_with_four_voting_members_is_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            members = [dict(m) for m in CORE_CHARTER_MEMBERS]
            members.append({"role": "game-architect", "votes": True})
            run = make_run(tmp, with_problem=False,
                           charter=charter_payload(members=members))
            r = engine.check(run)
            self.assertEqual(len(r["voting_members"]), 4)
            self.assertEqual(r["quorum"], 3)

    def test_explicit_quorum_is_honored(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False,
                           charter=charter_payload(quorum=2))
            self.assertEqual(engine.check(run)["quorum"], 2)
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False,
                           charter=charter_payload(quorum=3))
            self.assertEqual(engine.check(run)["quorum"], 3)

    def test_votes_false_member_excluded_from_quorum_math(self):
        with tempfile.TemporaryDirectory() as tmp:
            members = [dict(m) for m in CORE_CHARTER_MEMBERS]
            members.append({"role": "game-architect", "votes": False})
            run = make_run(tmp, with_problem=False,
                           charter=charter_payload(members=members))
            r = engine.check(run)
            self.assertEqual(r["voting_members"], list(VOTERS))
            self.assertEqual(r["quorum"], 2)

    def test_judge_never_a_voter_even_with_votes_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            members = [
                {"role": "librarian"},
                {"role": "judge", "votes": True},
                {"role": "contrarian"},
                {"role": "researcher"},
            ]
            run = make_run(tmp, with_problem=False,
                           charter=charter_payload(members=members))
            r = engine.check(run)
            self.assertEqual(r["voting_members"], list(VOTERS))
            self.assertEqual(r["quorum"], 2)


class TopicResolutionTest(unittest.TestCase):
    def test_support_at_quorum_without_unrebutted_refute_is_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            for role in VOTERS:
                append_finding(run, role, "t-01", "support")
            r = engine.check(run)
            t = r["topics"]["t-01"]
            self.assertEqual(t["state"], "resolved")
            self.assertEqual(t["support"], 3)
            self.assertEqual(t["quorum"], 2)
            self.assertEqual(t["un_rebutted_refutes"], [])
            self.assertEqual(r["contested_topics"], [])

    def test_unrebutted_refute_makes_topic_contested_despite_quorum_support(self):
        with tempfile.TemporaryDirectory() as tmp:
            members = [dict(m) for m in CORE_CHARTER_MEMBERS]
            members.append({"role": "game-architect", "votes": True})
            run = make_run(tmp, with_problem=False,
                           charter=charter_payload(members=members))
            for role in ("librarian", "researcher", "game-architect"):
                append_finding(run, role, "t-01", "support")
            append_finding(run, "contrarian", "t-01", "refute")
            r = engine.check(run)
            t = r["topics"]["t-01"]
            self.assertEqual(t["support"], 3)
            self.assertEqual(t["quorum"], 3)
            self.assertEqual(t["state"], "contested")
            self.assertEqual(len(t["un_rebutted_refutes"]), 1)
            self.assertIn("t-01", r["contested_topics"])

    def test_refute_without_evidence_does_not_block_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            for role in ("librarian", "researcher"):
                append_finding(run, role, "t-01", "support")
            append_finding(run, "contrarian", "t-01", "refute",
                           evidence=[])  # no evidence: not a blocking refute
            r = engine.check(run)
            self.assertEqual(r["topics"]["t-01"]["state"], "resolved")


class RebuttalTest(unittest.TestCase):
    def test_support_without_rebutting_field_does_not_rebut(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            append_finding(run, "researcher", "t-01", "support")          # f-001
            append_finding(run, "contrarian", "t-01", "refute")           # f-002
            append_finding(run, "librarian", "t-01", "support")           # f-003, no rebutting
            r = engine.check(run)
            t = r["topics"]["t-01"]
            self.assertEqual(t["state"], "contested")
            self.assertEqual(t["un_rebutted_refutes"], ["f-002"])

    def test_later_finding_listing_id_in_rebutting_rebuts_the_refute(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            append_finding(run, "researcher", "t-01", "support")          # f-001
            append_finding(run, "contrarian", "t-01", "refute")           # f-002
            append_finding(run, "librarian", "t-01", "support",           # f-003
                           rebutting=["f-002"])
            r = engine.check(run)
            t = r["topics"]["t-01"]
            self.assertEqual(t["un_rebutted_refutes"], [])
            self.assertEqual(t["support"], 2)
            self.assertEqual(t["quorum"], 2)
            self.assertEqual(t["state"], "resolved")

    def test_earlier_dangling_rebutting_reference_does_not_rebut(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            # f-001 references f-002 before it exists: an EARLIER seq can never
            # rebut a later refute, even if it names its id.
            append_finding(run, "researcher", "t-01", "support",           # f-001
                           rebutting=["f-002"])
            append_finding(run, "contrarian", "t-01", "refute")            # f-002
            append_finding(run, "librarian", "t-01", "support")            # f-003, no rebutting
            r = engine.check(run)
            self.assertEqual(r["topics"]["t-01"]["state"], "contested")
            self.assertEqual(r["topics"]["t-01"]["un_rebutted_refutes"], ["f-002"])


class PositionsTest(unittest.TestCase):
    def test_later_finding_flips_stance_for_position_math(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            append_finding(run, "researcher", "t-01", "support", round_no=1)  # f-001
            append_finding(run, "researcher", "t-01", "refute", round_no=2)   # f-002
            events = engine.read_events(run)
            pos_round1 = engine.positions_at(events, 1)
            pos_round2 = engine.positions_at(events, 2)
            self.assertEqual(pos_round1[("researcher", "t-01")], "support")
            self.assertEqual(pos_round2[("researcher", "t-01")], "refute")
            # the flip drops support below quorum: resolved -> contested
            append_finding(run, "librarian", "t-01", "support", round_no=1)
            r = engine.check(run)
            t = r["topics"]["t-01"]
            self.assertEqual(t["support"], 1)
            self.assertEqual(t["state"], "contested")


class RuledFreezeTest(unittest.TestCase):
    def test_ruled_topic_stays_rued_no_matter_what_findings_follow(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            append_finding(run, "librarian", "t-01", "support")
            append_finding(run, "contrarian", "t-01", "refute")
            engine.append_event(run, "ruling", ruling("t-01"), role="judge")
            r = engine.check(run)
            self.assertEqual(r["topics"]["t-01"]["state"], "ruled")
            self.assertEqual(r["ruled_topics"], ["t-01"])

            # more findings in a later round: support flips, new refutes land
            append_finding(run, "contrarian", "t-01", "support", round_no=2)
            append_finding(run, "researcher", "t-01", "refute", round_no=2)
            r = engine.check(run)
            self.assertEqual(r["topics"]["t-01"]["state"], "ruled")
            self.assertNotIn("t-01", r["contested_topics"])

            # even full support at quorum does not un-freeze a ruled topic
            append_finding(run, "researcher", "t-01", "support", round_no=3)
            append_finding(run, "librarian", "t-01", "support", round_no=3)
            r = engine.check(run)
            self.assertEqual(r["topics"]["t-01"]["state"], "ruled")
            self.assertEqual(r["topics"]["t-01"]["support"], 3)


class ImpasseTest(unittest.TestCase):
    def test_two_consecutive_no_progress_rounds_cause_impasse(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False,
                           charter=charter_payload(max_rounds=5))
            append_finding(run, "researcher", "t-01", "support", round_no=1)
            append_finding(run, "contrarian", "t-01", "refute", round_no=1)
            append_round_end(run, 1)
            append_round_end(run, 2)  # zero new findings, zero position changes
            append_round_end(run, 3)  # again
            r = engine.check(run)
            self.assertEqual(r["no_progress_streak"], 2)
            self.assertTrue(r["impasse"])
            # spec signal name (ARCHITECTURE 4.1): "no-progress"
            self.assertIn("no-progress", r["impasse_reason"])
            self.assertEqual(r["action"], "judge")

    def test_single_no_progress_round_is_not_impasse(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False,
                           charter=charter_payload(max_rounds=5))
            append_finding(run, "researcher", "t-01", "support", round_no=1)
            append_finding(run, "contrarian", "t-01", "refute", round_no=1)
            append_round_end(run, 1)
            append_round_end(run, 2)
            r = engine.check(run)
            self.assertEqual(r["no_progress_streak"], 1)
            self.assertFalse(r["impasse"])
            self.assertEqual(r["action"], "continue")

    def test_position_change_resets_no_progress_streak(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False,
                           charter=charter_payload(max_rounds=5))
            append_finding(run, "researcher", "t-01", "support", round_no=1)
            append_finding(run, "contrarian", "t-01", "refute", round_no=1)
            append_round_end(run, 1)
            # round 2: a stance flip is progress, even with no *new* topic findings
            append_finding(run, "researcher", "t-01", "refute", round_no=2)
            append_round_end(run, 2)
            append_round_end(run, 3)
            r = engine.check(run)
            # round 3 is no-progress, round 2 was progress: streak stops at 1
            self.assertEqual(r["no_progress_streak"], 1)
            self.assertFalse(r["impasse"])

    def test_max_rounds_reached_with_contested_topic_is_impasse_judge(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False,
                           charter=charter_payload(max_rounds=2))
            append_finding(run, "researcher", "t-01", "support", round_no=1)
            append_finding(run, "contrarian", "t-01", "refute", round_no=1)
            append_finding(run, "librarian", "t-01", "support", round_no=2)
            r = engine.check(run)
            self.assertEqual(r["round"], 2)
            self.assertTrue(r["impasse"])
            self.assertIn("max_rounds", r["impasse_reason"])
            self.assertEqual(r["action"], "judge")

    def test_action_recommend_when_nothing_contested(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False)
            for role in ("librarian", "researcher"):
                append_finding(run, role, "t-01", "support")
            r = engine.check(run)
            self.assertEqual(r["topics"]["t-01"]["state"], "resolved")
            self.assertEqual(r["contested_topics"], [])
            self.assertFalse(r["impasse"])
            self.assertEqual(r["action"], "recommend")

    def test_action_continue_when_contested_but_below_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False,
                           charter=charter_payload(max_rounds=3))
            append_finding(run, "researcher", "t-01", "support", round_no=1)
            append_finding(run, "contrarian", "t-01", "refute", round_no=1)
            r = engine.check(run)
            self.assertIn("t-01", r["contested_topics"])
            self.assertFalse(r["impasse"])
            self.assertEqual(r["action"], "continue")

    def test_ruled_only_topics_recommend(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = make_run(tmp, with_problem=False,
                           charter=charter_payload(max_rounds=1))
            append_finding(run, "librarian", "t-01", "support", round_no=1)
            append_finding(run, "contrarian", "t-01", "refute", round_no=1)
            engine.append_event(run, "ruling", ruling("t-01"), role="judge")
            r = engine.check(run)
            self.assertEqual(r["topics"]["t-01"]["state"], "ruled")
            self.assertEqual(r["contested_topics"], [])
            self.assertFalse(r["impasse"])
            self.assertEqual(r["action"], "recommend")


if __name__ == "__main__":
    unittest.main()
