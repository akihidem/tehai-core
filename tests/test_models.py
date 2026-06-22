import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from tehai.models import ModelTier, ScoreProfile, TaskContract, TaskType
from tehai.schema import validate_task_contract
from _util import mk_contract


class TestScoreProfile(unittest.TestCase):
    def test_weighted_is_not_complexity_only(self):
        # complexity 0 but other axes high -> weighted score must be > 0.
        s = ScoreProfile(complexity=0, ambiguity=100, risk=100, context_size=100,
                         tool_risk=100, domain_specialization=100)
        self.assertGreater(s.weighted_score(), 50)

    def test_weights_sum_to_one(self):
        from tehai.models import ROUTER_WEIGHTS
        self.assertAlmostEqual(sum(ROUTER_WEIGHTS.values()), 1.0, places=6)

    def test_out_of_range_flagged(self):
        s = ScoreProfile(complexity=200)
        self.assertTrue(s.errors())


class TestTaskContractValidation(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(mk_contract().validate(), [])

    def test_vague_objective_rejected(self):
        c = mk_contract(objective="全部いい感じに実装する")
        self.assertTrue(any("vague" in e for e in c.validate()))

    def test_missing_expected_output(self):
        c = mk_contract(expected_output=[])
        self.assertTrue(any("expected_output" in e for e in c.validate()))

    def test_missing_acceptance(self):
        c = mk_contract(acceptance_criteria=[])
        self.assertTrue(any("acceptance_criteria" in e for e in c.validate()))

    def test_missing_escalation(self):
        c = mk_contract(escalation_conditions=[])
        self.assertTrue(any("escalation_conditions" in e for e in c.validate()))

    def test_self_dependency(self):
        c = mk_contract(task_id="X", dependencies=["X"])
        self.assertTrue(any("depend on itself" in e for e in c.validate()))

    def test_self_parent(self):
        c = mk_contract(task_id="X", parent_task_id="X")
        self.assertTrue(any("its own parent" in e for e in c.validate()))


class TestSerialization(unittest.TestCase):
    def test_roundtrip(self):
        c = mk_contract(recommended_model=ModelTier.LARGE, assigned_agent_template="BackendEngineer")
        d = c.to_dict()
        c2 = TaskContract.from_dict(d)
        self.assertEqual(c2.to_dict(), d)

    def test_dict_matches_schema(self):
        c = mk_contract(recommended_model=ModelTier.MEDIUM)
        self.assertEqual(validate_task_contract(c.to_dict()), [])


if __name__ == "__main__":
    unittest.main()
