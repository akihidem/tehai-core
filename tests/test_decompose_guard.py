import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from tehai.decompose_guard import (
    DecompositionGuard, DecompositionState, GuardConfig, RejectReason,
)
from tehai.models import ScoreProfile, TaskType
from _util import mk_contract


def child(task_id, objective, **over):
    base = dict(
        task_id=task_id, objective=objective, task_type=TaskType.CODE_IMPLEMENTATION,
        expected_output=["validation.ts"], acceptance_criteria=["ok"],
        escalation_conditions=["esc"], estimated_steps=3, depth=1,
    )
    base.update(over)
    return mk_contract(**base)


class TestDecompositionGuard(unittest.TestCase):
    def setUp(self):
        self.guard = DecompositionGuard()
        self.parent = mk_contract(task_id="P", estimated_steps=8, expected_output=["validation.ts"], depth=0)

    def _kids(self):
        return [child("P-a", "implement core validation"),
                child("P-b", "write boundary tests for validation")]

    def test_happy_path_allowed(self):
        st = DecompositionState()
        d = self.guard.can_decompose(self.parent, self._kids(), st)
        self.assertTrue(d.allowed, d.detail)
        self.guard.register(self._kids(), st)
        self.assertEqual(st.delegations_used, 2)

    def test_no_children(self):
        d = self.guard.can_decompose(self.parent, [], DecompositionState())
        self.assertEqual(d.reason, RejectReason.NO_CHILDREN)

    def test_max_depth(self):
        deep = mk_contract(task_id="P", depth=3, estimated_steps=8, expected_output=["validation.ts"])
        d = self.guard.can_decompose(deep, self._kids(), DecompositionState())
        self.assertEqual(d.reason, RejectReason.MAX_DEPTH)

    def test_max_delegations(self):
        st = DecompositionState(delegations_used=20)
        d = self.guard.can_decompose(self.parent, self._kids(), st)
        self.assertEqual(d.reason, RejectReason.MAX_DELEGATIONS)

    def test_max_concurrency(self):
        st = DecompositionState(active_agents=8)
        d = self.guard.can_decompose(self.parent, self._kids(), st)
        self.assertEqual(d.reason, RejectReason.MAX_CONCURRENCY)

    def test_over_budget(self):
        g = DecompositionGuard(GuardConfig(max_cost_per_run=0.0))
        d = g.can_decompose(self.parent, self._kids(), DecompositionState(), estimated_cost=1.0)
        self.assertEqual(d.reason, RejectReason.OVER_BUDGET)

    def test_not_smaller(self):
        kids = [child("P-a", "core", estimated_steps=8), child("P-b", "tests", estimated_steps=3)]
        d = self.guard.can_decompose(self.parent, kids, DecompositionState())
        self.assertEqual(d.reason, RejectReason.NOT_SMALLER)

    def test_duplicate_in_batch(self):
        kids = [child("P-a", "same objective text"), child("P-b", "same objective text")]
        d = self.guard.can_decompose(self.parent, kids, DecompositionState())
        self.assertEqual(d.reason, RejectReason.DUPLICATE_OBJECTIVE)

    def test_duplicate_in_history(self):
        st = DecompositionState(seen_objectives={"implement core validation"})
        d = self.guard.can_decompose(self.parent, self._kids(), st)
        self.assertEqual(d.reason, RejectReason.DUPLICATE_OBJECTIVE)

    def test_cyclic(self):
        a = child("P-a", "core", dependencies=["P-b"])
        b = child("P-b", "tests", dependencies=["P-a"])
        d = self.guard.can_decompose(self.parent, [a, b], DecompositionState())
        self.assertEqual(d.reason, RejectReason.CYCLIC_DEPENDENCY)

    def test_uncontractable_child(self):
        bad = child("P-a", "core", expected_output=[])  # invalid contract
        ok = child("P-b", "tests")
        d = self.guard.can_decompose(self.parent, [bad, ok], DecompositionState())
        self.assertEqual(d.reason, RejectReason.UNCONTRACTABLE)

    def test_no_progress(self):
        kids = [child("P-a", "unrelated thing one", expected_output=["other.md"]),
                child("P-b", "unrelated thing two", expected_output=["misc.md"])]
        d = self.guard.can_decompose(self.parent, kids, DecompositionState())
        self.assertEqual(d.reason, RejectReason.NO_PROGRESS)

    def test_retry_cap(self):
        st = DecompositionState()
        self.assertTrue(self.guard.can_retry("T", st))
        for _ in range(3):
            self.guard.record_retry("T", st)
        self.assertFalse(self.guard.can_retry("T", st))


if __name__ == "__main__":
    unittest.main()
