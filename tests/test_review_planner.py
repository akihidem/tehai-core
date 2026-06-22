import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from tehai.models import ReviewLens, TaskType
from tehai.review_planner import ReviewPlanner
from _util import mk_contract


class TestReviewPlanner(unittest.TestCase):
    def setUp(self):
        self.rp = ReviewPlanner()

    def _lenses(self, plan):
        return {s.lens for s in plan.steps}

    def test_low_risk_auto_check_only(self):
        c = mk_contract(objective="fix typos and unify notation", task_type=TaskType.DOC_FORMATTING)
        plan = self.rp.plan(c)
        self.assertEqual(self._lenses(plan), {ReviewLens.AUTO_CHECK})
        self.assertFalse(plan.require_judge)
        self.assertFalse(plan.require_human_gate)

    def test_security_type_gets_security_and_judge(self):
        c = mk_contract(objective="review the auth flow", task_type=TaskType.SECURITY_REVIEW)
        plan = self.rp.plan(c)
        self.assertIn(ReviewLens.SECURITY, self._lenses(plan))
        self.assertTrue(plan.require_judge)

    def test_hazard_objective_human_gate(self):
        c = mk_contract(objective="本番にデプロイして秘密鍵を更新する", task_type=TaskType.CODE_IMPLEMENTATION)
        plan = self.rp.plan(c)
        self.assertTrue(plan.require_human_gate)
        self.assertIn(ReviewLens.SECURITY, self._lenses(plan))

    def test_normal_implementation(self):
        c = mk_contract(objective="add a pagination helper", task_type=TaskType.CODE_IMPLEMENTATION)
        plan = self.rp.plan(c)
        self.assertEqual(
            self._lenses(plan),
            {ReviewLens.AUTO_CHECK, ReviewLens.REQUIREMENTS, ReviewLens.EDGE_CASES},
        )
        self.assertFalse(plan.require_human_gate)

    def test_release_human_gate(self):
        c = mk_contract(objective="cut the release", task_type=TaskType.RELEASE)
        plan = self.rp.plan(c)
        self.assertTrue(plan.require_human_gate)
        self.assertTrue(plan.require_judge)

    def test_ui_adds_ux(self):
        c = mk_contract(objective="implement the login 画面 form", task_type=TaskType.CODE_IMPLEMENTATION,
                        assigned_agent_template="FrontendEngineer")
        plan = self.rp.plan(c)
        self.assertIn(ReviewLens.UX, self._lenses(plan))


if __name__ == "__main__":
    unittest.main()
