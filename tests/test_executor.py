import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from tehai.backends import ModelBackend
from tehai.decompose_guard import DecompositionGuard, DecompositionState, GuardConfig
from tehai.executor import ExecutionResult, Executor
from tehai.judge import Judge
from tehai.logger import ExecutionLogger
from tehai.model_router import ModelRouter
from tehai.models import JudgeDecision, TaskStatus, TaskType
from tehai.orchestrator import Orchestrator
from tehai.permissions import PermissionModel
from tehai.registry import AgentRegistry
from tehai.review_planner import ReviewPlanner
from tehai.reviewer import Reviewer
from tehai.schema import validate_log_record
from _util import mk_contract


class Fixed(ModelBackend):
    name = "fixed"
    available = True

    def __init__(self, payload):
        self.payload = payload

    def complete(self, prompt, tier, **kw):
        return self.payload


def build_executor(backend=None, reviewer=None, guard=None, permissions=None):
    return Executor(
        AgentRegistry.load(),
        reviewer or Reviewer(None),
        Judge(),
        ReviewPlanner(),
        ModelRouter(),
        permissions or PermissionModel(),
        guard or DecompositionGuard(),
        backend,
    )


class TestGeneration(unittest.TestCase):
    def test_deterministic_stub_names_match_expected_output(self):
        ex = build_executor(backend=None)
        c = mk_contract(expected_output=["validation.ts", "validation.test.ts"],
                        acceptance_criteria=["rejects empty", "handles boundary limit"])
        r = ex.execute_task(c, {}, DecompositionState())
        self.assertEqual(set(r.artifacts), {"validation.ts", "validation.test.ts"})
        self.assertIn("objective:", r.artifacts["validation.ts"])

    def test_llm_generation_parsed(self):
        ex = build_executor(backend=Fixed('{"validation.ts":"export const ok = true"}'))
        c = mk_contract(expected_output=["validation.ts"],
                        acceptance_criteria=["rejects empty", "handles boundary limit"])
        r = ex.execute_task(c, {}, DecompositionState())
        self.assertEqual(r.artifacts["validation.ts"], "export const ok = true")

    def test_llm_garbage_falls_back_to_stub(self):
        ex = build_executor(backend=Fixed("not json at all"))
        c = mk_contract(expected_output=["validation.ts"],
                        acceptance_criteria=["rejects empty", "handles boundary limit"])
        r = ex.execute_task(c, {}, DecompositionState())
        self.assertIn("validation.ts", r.artifacts)
        self.assertIn("TODO", r.artifacts["validation.ts"])


class TestPermissionGate(unittest.TestCase):
    def test_dangerous_action_needs_approval_escalates(self):
        pm = PermissionModel()
        ex = build_executor(permissions=pm)
        c = mk_contract(required_tools=["production_deploy"], assigned_agent_template="ReleaseManager",
                        task_type=TaskType.RELEASE, objective="cut the production release")
        r = ex.execute_task(c, {}, DecompositionState())
        self.assertEqual(r.status, TaskStatus.ESCALATED)
        self.assertEqual(r.approval_required, "production_deploy")
        self.assertEqual(r.attempts, 0)
        self.assertEqual(len(pm.pending_approvals), 1)

    def test_forbidden_action_escalates_without_approval(self):
        pm = PermissionModel()
        ex = build_executor(permissions=pm)
        c = mk_contract(required_tools=["git_push"], assigned_agent_template="BackendEngineer")
        r = ex.execute_task(c, {}, DecompositionState())
        self.assertEqual(r.status, TaskStatus.ESCALATED)
        self.assertEqual(r.approval_required, "git_push")
        self.assertEqual(len(pm.pending_approvals), 0)  # forbidden != approvable


class TestFSM(unittest.TestCase):
    def _benign(self):
        return mk_contract(objective="add a pagination helper",
                           acceptance_criteria=["returns page slices", "handles boundary at the limit"])

    def test_accept_completes(self):
        ex = build_executor(backend=None, reviewer=Reviewer(None))
        r = ex.execute_task(self._benign(), {}, DecompositionState())
        self.assertEqual(r.status, TaskStatus.COMPLETED)
        self.assertEqual(r.decision.decision, JudgeDecision.ACCEPT)

    def test_critical_review_discards_to_failed(self):
        rv = Reviewer(Fixed('{"verdict":"fail","severity":"critical","rationale":"broken","findings":["x"]}'))
        ex = build_executor(backend=None, reviewer=rv)
        r = ex.execute_task(self._benign(), {}, DecompositionState())
        self.assertEqual(r.status, TaskStatus.FAILED)
        self.assertEqual(r.attempts, 1)  # discard never retries

    def test_revise_retries_then_escalates(self):
        rv = Reviewer(Fixed('{"verdict":"fail","severity":"medium","rationale":"nope","findings":["x"]}'))
        ex = build_executor(backend=None, reviewer=rv, guard=DecompositionGuard(GuardConfig(max_retries=2)))
        r = ex.execute_task(self._benign(), {}, DecompositionState())
        self.assertEqual(r.status, TaskStatus.ESCALATED)
        self.assertEqual(r.attempts, 3)  # initial + 2 retries

    def test_human_gate_accept_escalates(self):
        ex = build_executor(backend=None, reviewer=Reviewer(None))
        c = mk_contract(objective="本番にデプロイする設定変更を実装する",
                        acceptance_criteria=["rejects invalid input", "handles boundary at limit"],
                        constraints=["秘密情報をログに出力しない"])
        r = ex.execute_task(c, {}, DecompositionState())
        self.assertEqual(r.decision.decision, JudgeDecision.ACCEPT)
        self.assertEqual(r.status, TaskStatus.ESCALATED)  # awaiting human approval


class Usage(ModelBackend):
    name = "usage"
    available = True

    def complete(self, prompt, tier, **kw):
        self.last_usage = {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300}
        return json.dumps({"out.py": "x = 1\n"})


class TestActualCost(unittest.TestCase):
    def test_actual_cost_from_token_usage(self):
        ex = build_executor(backend=Usage())
        c = mk_contract(expected_output=["out.py"], acceptance_criteria=["adds", "handles boundary"])
        r = ex.execute_task(c, {}, DecompositionState())
        self.assertIsNotNone(r.actual_cost)
        self.assertAlmostEqual(r.actual_cost, 300 * 0.00005, places=6)

    def test_no_usage_no_cost(self):
        ex = build_executor(backend=None)  # deterministic stub, no tokens
        r = ex.execute_task(mk_contract(acceptance_criteria=["a", "b boundary"]), {}, DecompositionState())
        self.assertIsNone(r.actual_cost)


class TestExecutePlan(unittest.TestCase):
    def test_plan_runs_and_threads_upstream(self):
        orch = Orchestrator.default("null")
        plan = orch.plan("add a pagination helper to the table component")
        with tempfile.TemporaryDirectory() as d:
            logger = ExecutionLogger(Path(d) / "exec.jsonl")
            results = orch.execute(plan, logger=logger)
            self.assertEqual(set(results), set(plan.execution_order))
            # every executed task produced artifacts
            for r in results.values():
                self.assertTrue(r.artifacts)
                self.assertIsNotNone(r.provenance)
            # at least one dependent task consumed an upstream artifact
            self.assertTrue(any(r.provenance.derived_from for r in results.values()))
            # ledger rows are schema-valid and carry real elapsed timing
            rows = logger.read()
            for row in rows:
                self.assertEqual(validate_log_record(row), [])
            self.assertTrue(any(row.get("elapsed_seconds") is not None for row in rows))
            self.assertTrue(any(row.get("estimated_seconds") is not None for row in rows))

    def test_limit_bounds_tasks(self):
        orch = Orchestrator.default("null")
        plan = orch.plan("add a pagination helper to the table component")
        results = orch.execute(plan, limit=2)
        self.assertEqual(len(results), 2)


if __name__ == "__main__":
    unittest.main()
