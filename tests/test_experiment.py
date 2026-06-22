"""Smoke + invariant tests for the A-vs-B experiment harness.

These do not assert exact numbers (those may shift as the cost model evolves);
they assert the structural invariants the experiment must preserve to be a fair,
meaningful comparison.
"""

import json
import unittest

from experiments.org_vs_dataflow import run_suite

_AGG_KEYS = {"true_success_rate", "escaped_defects", "total_model_calls",
            "total_cost_usd", "human_intervention_rate"}


class ExperimentHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_suite("null")

    def test_both_architectures_report_all_three_metric_families(self):
        agg = self.report["aggregate"]
        self.assertTrue(_AGG_KEYS.issubset(agg["A_human_org"]))
        self.assertTrue(_AGG_KEYS.issubset(agg["B_flat_dataflow"]))
        for r in self.report["scenarios"]:
            self.assertGreater(r["A"]["model_calls"], 0)
            self.assertGreater(r["B"]["model_calls"], 0)

    def test_every_injected_defect_lands_on_a_real_node(self):
        # Fairness: a defect that matches no B node would be a silent free pass.
        for r in self.report["scenarios"]:
            self.assertEqual(r["B"]["unmatched_defect_stages"], [],
                             f"{r['key']}: injected defect matched no flat-dataflow node")

    def test_flat_dataflow_never_lets_a_blind_spot_escape(self):
        self.assertEqual(self.report["aggregate"]["B_flat_dataflow"]["escaped_defects"], 0)

    def test_human_org_escapes_at_least_one_blind_spot(self):
        # The doc-only scenario has no independent downstream verifier -> A ships it.
        self.assertGreaterEqual(self.report["aggregate"]["A_human_org"]["escaped_defects"], 1)

    def test_flat_dataflow_is_cheaper_overall(self):
        a = self.report["aggregate"]["A_human_org"]["total_cost_usd"]
        b = self.report["aggregate"]["B_flat_dataflow"]["total_cost_usd"]
        self.assertLess(b, a)

    def test_flat_dataflow_reliability_not_worse(self):
        a = self.report["aggregate"]["A_human_org"]["true_success_rate"]
        b = self.report["aggregate"]["B_flat_dataflow"]["true_success_rate"]
        self.assertGreaterEqual(b, a)

    def test_suite_is_deterministic(self):
        again = run_suite("null")
        self.assertEqual(json.dumps(self.report, sort_keys=True),
                         json.dumps(again, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
