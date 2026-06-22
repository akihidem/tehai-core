import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from tehai.models import Effort, ModelTier, ScoreProfile, TaskType
from tehai.model_router import ModelRouter
from _util import mk_contract


class TestModelRouter(unittest.TestCase):
    def setUp(self):
        self.router = ModelRouter()

    def test_benign_low_scores_small(self):
        c = mk_contract(
            objective="extract product names from a CSV file",
            task_type=TaskType.EXTRACTION,
            scores=ScoreProfile(complexity=10, ambiguity=10, risk=5,
                                context_size=10, tool_risk=5, domain_specialization=10),
        )
        self.assertEqual(self.router.route(c).tier, ModelTier.SMALL)

    def test_low_complexity_high_risk_hazard_forces_large(self):
        # Criterion 3: complexity alone is low, but hazard -> LARGE.
        c = mk_contract(
            objective="本番にデプロイする小さな設定変更",
            task_type=TaskType.GENERIC,
            scores=ScoreProfile(complexity=5, ambiguity=5, risk=5,
                                context_size=5, tool_risk=5, domain_specialization=5),
        )
        self.assertEqual(self.router.route(c).tier, ModelTier.LARGE)

    def test_high_risk_score_forces_large(self):
        c = mk_contract(
            objective="rotate credentials",
            scores=ScoreProfile(complexity=10, ambiguity=10, risk=75,
                                context_size=10, tool_risk=10, domain_specialization=10),
        )
        self.assertEqual(self.router.route(c).tier, ModelTier.LARGE)

    def test_high_stakes_type_forces_large(self):
        c = mk_contract(objective="design the module boundaries", task_type=TaskType.ARCHITECTURE,
                        scores=ScoreProfile(complexity=10, ambiguity=10, risk=10,
                                            context_size=10, tool_risk=10, domain_specialization=10))
        self.assertEqual(self.router.route(c).tier, ModelTier.LARGE)

    def test_weighted_not_complexity_only(self):
        # complexity low, but ambiguity+risk high -> should climb above SMALL.
        c = mk_contract(
            objective="reconcile an ambiguous spec",
            task_type=TaskType.GENERIC,
            scores=ScoreProfile(complexity=10, ambiguity=95, risk=60,
                                context_size=40, tool_risk=20, domain_specialization=40),
        )
        self.assertNotEqual(self.router.route(c).tier, ModelTier.SMALL)

    def test_large_context_escalates(self):
        c = mk_contract(
            objective="summarize a large codebase",
            task_type=TaskType.SUMMARIZATION,
            estimated_context_tokens=60000,
            scores=ScoreProfile(complexity=20, ambiguity=20, risk=10,
                                context_size=90, tool_risk=10, domain_specialization=20),
        )
        self.assertNotEqual(self.router.route(c).tier, ModelTier.SMALL)

    def test_consecutive_failures_escalate(self):
        c = mk_contract(
            objective="small refactor",
            task_type=TaskType.GENERIC,
            scores=ScoreProfile(complexity=10, ambiguity=10, risk=10,
                                context_size=10, tool_risk=10, domain_specialization=10),
        )
        base = self.router.route(c, consecutive_failures=0).tier
        escalated = self.router.route(c, consecutive_failures=2).tier
        self.assertEqual(base, ModelTier.SMALL)
        self.assertEqual(escalated, ModelTier.MEDIUM)


class TestEffortRouting(unittest.TestCase):
    def setUp(self):
        self.router = ModelRouter()

    def _c(self, objective="do a thing", task_type=TaskType.GENERIC, **sc):
        base = dict(complexity=10, ambiguity=10, risk=10, context_size=10,
                    tool_risk=10, domain_specialization=10)
        base.update(sc)
        return mk_contract(objective=objective, task_type=task_type, scores=ScoreProfile(**base))

    def test_low_effort_for_trivial(self):
        self.assertEqual(self.router.route(self._c(task_type=TaskType.EXTRACTION)).effort, Effort.LOW)

    def test_high_plus_for_hard_ambiguous(self):
        c = self._c(complexity=80, ambiguity=85, domain_specialization=70)
        self.assertGreaterEqual(self.router.route(c).effort.rank, Effort.HIGH.rank)

    def test_hazard_escalates_effort(self):
        c = self._c(objective="本番にデプロイする")
        self.assertGreaterEqual(self.router.route(c).effort.rank, Effort.HIGH.rank)

    def test_consecutive_failures_bump_effort(self):
        c = self._c()
        base = self.router.route(c, consecutive_failures=0).effort
        esc = self.router.route(c, consecutive_failures=2).effort
        self.assertGreater(esc.rank, base.rank)


if __name__ == "__main__":
    unittest.main()
