"""Tests for the FlatDataflowOrchestrator (B) — the verification-centric flat path."""

import unittest

from tehai import Defect, FlatDataflowOrchestrator, FlatRunResult


_IMPL_GOAL = "ユーザー登録のメール形式バリデーションを実装する"
_METRIC_KEYS = {
    "system_type", "task_success", "true_success", "autonomous_completion",
    "human_intervention_required", "human_intervention_rate", "escaped_defects",
    "node_count", "model_calls", "cost_usd", "local_retries",
}


class FlatDataflowTests(unittest.TestCase):
    def setUp(self):
        self.flow = FlatDataflowOrchestrator.default("null")

    def test_clean_run_completes_with_full_metric_surface(self):
        res = self.flow.run(_IMPL_GOAL)
        self.assertIsInstance(res, FlatRunResult)
        self.assertEqual(res.final_status, "completed")
        self.assertEqual(res.escaped_defects, 0)
        self.assertFalse(res.human_intervention_required)
        self.assertGreater(res.model_calls, 0)
        self.assertTrue(_METRIC_KEYS.issubset(res.metrics))
        self.assertTrue(res.metrics["true_success"])

    def test_empty_goal_raises(self):
        with self.assertRaises(ValueError):
            self.flow.run("   ")

    def test_overt_defect_resolved_by_node_local_retry(self):
        res = self.flow.run(_IMPL_GOAL, injected_defects={
            "implementation": Defect("implementation_error", resolve_after=1)})
        self.assertEqual(res.final_status, "completed")
        self.assertFalse(res.human_intervention_required)
        self.assertGreater(res.local_retries, 0)          # it actually re-ran the node
        self.assertTrue(res.metrics["true_success"])
        # a re-run node costs strictly more than the clean baseline
        self.assertGreater(res.model_calls, self.flow.run(_IMPL_GOAL).model_calls)

    def test_blind_spot_caught_at_node_no_escape(self):
        res = self.flow.run(_IMPL_GOAL, injected_defects={
            "implementation": Defect("security_risk", resolve_after=1, blind_spot=True)})
        # B verifies every node externally, so a blind-spot never escapes.
        self.assertEqual(res.escaped_defects, 0)
        self.assertTrue(res.metrics["true_success"])
        rec = next(n for n in res.node_records if n["stage"] == "implementation")
        self.assertTrue(rec["caught_at_node"])

    def test_defect_beyond_local_cap_escalates_to_human(self):
        res = self.flow.run(_IMPL_GOAL, injected_defects={
            "implementation": Defect("implementation_error", resolve_after=4)})
        self.assertTrue(res.human_intervention_required)   # exceeded local retry cap
        self.assertEqual(res.final_status, "needs_human")
        self.assertFalse(res.metrics["task_success"])

    def test_run_is_deterministic(self):
        a = self.flow.run(_IMPL_GOAL, injected_defects={"implementation": Defect("implementation_error", 2)})
        b = self.flow.run(_IMPL_GOAL, injected_defects={"implementation": Defect("implementation_error", 2)})
        self.assertEqual(a.to_dict(), b.to_dict())


if __name__ == "__main__":
    unittest.main()
