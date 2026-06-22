import unittest

from tehai.evaluation import Calibration, EvaluationStore
from tehai.model_router import SMALL_MAX


def rec(model, decision="accept", **extra):
    d = {"run_id": "R", "task_id": "T", "task_type": "code_implementation",
         "selected_model": model, "judge_decision": decision}
    d.update(extra)
    return d


class TestEvaluationMetrics(unittest.TestCase):
    def test_success_rate_and_grouping(self):
        recs = [rec("small", "accept"), rec("small", "revise"), rec("large", "accept")]
        m = EvaluationStore().compute(recs)
        self.assertEqual(m.n_records, 3)
        self.assertAlmostEqual(m.overall_success_rate, 2 / 3, places=3)
        self.assertEqual(m.by_model["large"], 1.0)

    def test_failure_reason_counts_as_failure(self):
        m = EvaluationStore().compute([rec("small", "accept", failure_reason="boom")])
        self.assertEqual(m.overall_success_rate, 0.0)


class TestCalibration(unittest.TestCase):
    def test_low_small_success_lowers_threshold(self):
        recs = [rec("small", "revise")] * 7 + [rec("small", "accept")] * 3  # 0.3 success
        cal = EvaluationStore().calibrate(recs)
        self.assertIsInstance(cal, Calibration)
        self.assertLess(cal.proposed["router_small_max"], cal.current["router_small_max"])
        self.assertTrue(any("small" in r for r in cal.rationale))
        self.assertIn("NOT auto-applied", cal.status)

    def test_healthy_metrics_propose_nothing(self):
        cal = EvaluationStore().calibrate([rec("small", "accept")] * 5)
        self.assertEqual(cal.proposed["router_small_max"], SMALL_MAX)
        self.assertTrue(any("no parameter changes" in r for r in cal.rationale))

    def test_observed_cost_and_seconds_surface(self):
        recs = [rec("medium", "accept", actual_cost=0.1, elapsed_seconds=5.0),
                rec("medium", "accept", actual_cost=0.3, elapsed_seconds=9.0)]
        cal = EvaluationStore().calibrate(recs)
        self.assertIn("observed_tier_cost", cal.proposed)
        self.assertAlmostEqual(cal.proposed["observed_tier_cost"]["medium"], 0.2, places=3)
        self.assertIn("observed_tier_seconds", cal.proposed)


if __name__ == "__main__":
    unittest.main()
