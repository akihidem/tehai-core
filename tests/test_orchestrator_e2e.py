import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from tehai.logger import ExecutionLogger
from tehai.evaluation import EvaluationStore
from tehai.models import ModelTier, TaskStatus
from tehai.orchestrator import Orchestrator, OrchestratorError, topological_order
from tehai.schema import validate_task_contract, validate_log_record
from _util import mk_contract

LOGIN = "ログイン画面に入力バリデーションを実装する。メール形式とパスワード長を検証する"
BENIGN = "READMEの誤字を修正して表記ゆれを統一する"
HAZARD = "決済APIに認証トークン検証を追加し、本番にデプロイする"


class TestOrchestratorE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.orch = Orchestrator.default()
        cls.plan = cls.orch.plan(LOGIN)

    def test_at_least_three_contracts(self):
        self.assertGreaterEqual(len(self.plan.contracts), 3)

    def test_every_contract_valid(self):
        for c in self.plan.contracts:
            self.assertEqual(c.validate(), [], c.task_id)

    def test_every_contract_schema_conformant(self):
        for c in self.plan.contracts:
            self.assertEqual(validate_task_contract(c.to_dict()), [], c.task_id)

    def test_every_contract_has_agent_and_model_and_review(self):
        for c in self.plan.contracts:
            self.assertIn(c.assigned_agent_template, self.orch.registry)
            self.assertIsInstance(c.recommended_model, ModelTier)
            self.assertIn(c.task_id, self.plan.review_plans)

    def test_every_contract_has_effort(self):
        from tehai.models import Effort
        for c in self.plan.contracts:
            self.assertIsInstance(c.recommended_effort, Effort)
        # the hazardous (login) plan should route at least one task to high+ effort
        self.assertTrue(any(c.recommended_effort.rank >= Effort.HIGH.rank for c in self.plan.contracts))

    def test_topological_order_respects_dependencies(self):
        pos = {tid: i for i, tid in enumerate(self.plan.execution_order)}
        by_id = {c.task_id: c for c in self.plan.contracts}
        self.assertEqual(len(pos), len(self.plan.contracts))
        for c in self.plan.contracts:
            for dep in c.dependencies:
                if dep in by_id:
                    self.assertLess(pos[dep], pos[c.task_id], f"{dep} must precede {c.task_id}")

    def test_dependencies_reference_existing_tasks(self):
        ids = {c.task_id for c in self.plan.contracts}
        for c in self.plan.contracts:
            for dep in c.dependencies:
                self.assertIn(dep, ids, f"{c.task_id} depends on unknown {dep}")

    def test_cycle_raises(self):
        a = mk_contract(task_id="A", dependencies=["B"])
        b = mk_contract(task_id="B", dependencies=["A"])
        with self.assertRaises(OrchestratorError):
            topological_order([a, b])

    def test_empty_request_rejected(self):
        with self.assertRaises(OrchestratorError):
            self.orch.plan("   ")

    def test_sample_log_written_and_schema_valid(self):
        with tempfile.TemporaryDirectory() as d:
            logger = ExecutionLogger(Path(d) / "run.jsonl")
            records = self.orch.emit_sample_log(self.plan, logger)
            self.assertEqual(len(records), len(self.plan.execution_order))
            for row in logger.read():
                self.assertEqual(validate_log_record(row), [])
            metrics = EvaluationStore().compute(logger.read())
            self.assertEqual(metrics.n_records, len(records))

    def test_benign_uses_a_cheap_tier(self):
        plan = self.orch.plan(BENIGN)
        tiers = {c.recommended_model for c in plan.contracts}
        self.assertIn(ModelTier.SMALL, tiers, "benign request should route some task to SMALL")

    def test_hazard_request_triggers_human_gate(self):
        plan = self.orch.plan(HAZARD)
        self.assertTrue(any(rp.require_human_gate for rp in plan.review_plans.values()))

    def test_deterministic_run_id(self):
        self.assertEqual(self.orch.plan(LOGIN).run_id, self.orch.plan(LOGIN).run_id)

    def test_recursion_depth_respected(self):
        # No contract should exceed the guard's max depth.
        max_depth = self.orch.guard.config.max_depth
        for c in self.plan.contracts:
            self.assertLessEqual(c.depth, max_depth)


if __name__ == "__main__":
    unittest.main()
