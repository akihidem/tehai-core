import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from tehai.judge import Judge
from tehai.models import JudgeDecision, ReviewLens, ReviewResult
from _util import mk_contract


def rr(lens, verdict, severity="none", rationale="", findings=None):
    return ReviewResult(task_id="T-1", lens=lens, verdict=verdict, severity=severity,
                        rationale=rationale, findings=findings or [])


class TestJudge(unittest.TestCase):
    def setUp(self):
        self.judge = Judge()
        self.c = mk_contract()

    def test_no_reviews_accept(self):
        self.assertEqual(self.judge.decide(self.c, []).decision, JudgeDecision.ACCEPT)

    def test_all_pass_accept(self):
        reviews = [rr(ReviewLens.REQUIREMENTS, "pass"), rr(ReviewLens.EDGE_CASES, "pass")]
        self.assertEqual(self.judge.decide(self.c, reviews).decision, JudgeDecision.ACCEPT)

    def test_concerns_accept(self):
        reviews = [rr(ReviewLens.REQUIREMENTS, "pass"), rr(ReviewLens.EDGE_CASES, "concerns")]
        self.assertEqual(self.judge.decide(self.c, reviews).decision, JudgeDecision.ACCEPT)

    def test_medium_fail_revise(self):
        reviews = [rr(ReviewLens.EDGE_CASES, "fail", "medium")]
        self.assertEqual(self.judge.decide(self.c, reviews).decision, JudgeDecision.REVISE)

    def test_critical_fail_discard(self):
        reviews = [rr(ReviewLens.REQUIREMENTS, "fail", "critical")]
        self.assertEqual(self.judge.decide(self.c, reviews).decision, JudgeDecision.DISCARD)

    def test_security_high_fail_discard(self):
        reviews = [rr(ReviewLens.SECURITY, "fail", "high")]
        self.assertEqual(self.judge.decide(self.c, reviews).decision, JudgeDecision.DISCARD)

    def test_transient_rerun(self):
        reviews = [rr(ReviewLens.AUTO_CHECK, "fail", "medium", rationale="test was flaky/timeout")]
        self.assertEqual(self.judge.decide(self.c, reviews).decision, JudgeDecision.RERUN)

    def test_reason_recorded(self):
        d = self.judge.decide(self.c, [rr(ReviewLens.EDGE_CASES, "fail", "low")])
        self.assertTrue(d.reason)
        self.assertTrue(d.basis)


if __name__ == "__main__":
    unittest.main()
