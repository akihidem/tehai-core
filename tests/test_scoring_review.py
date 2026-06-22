import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from tehai.backends import ModelBackend
from tehai.judge import Judge
from tehai.models import (
    JudgeDecision, ModelTier, ReviewLens, ReviewPlan, ReviewStep, ScoreProfile, TaskType,
)
from tehai.orchestrator import Orchestrator
from tehai.review_planner import ReviewPlanner
from tehai.reviewer import Reviewer
from tehai.scoring import Scorer
from _util import mk_contract


class FixedBackend(ModelBackend):
    name = "fixed"
    available = True

    def __init__(self, payload):
        self.payload = payload

    def complete(self, prompt, tier, **kw):
        return self.payload


class SmartBackend(ModelBackend):
    """Returns garbage for decomposition (forces template path) but real scores
    for the scoring prompt (keyed by the ids it finds in the prompt)."""

    name = "smart"
    available = True

    def complete(self, prompt, tier, **kw):
        if "Score each TASK" in prompt:
            ids = re.findall(r'- "([^"]+)":', prompt)
            return json.dumps({i: {"complexity": 50, "ambiguity": 50, "risk": 50,
                                   "context_size": 50, "tool_risk": 50,
                                   "domain_specialization": 50} for i in ids})
        return "not a decomposition"


# --------------------------------------------------------------------------- #
class TestScorer(unittest.TestCase):
    def setUp(self):
        self.contracts = [mk_contract(task_id="T-1"), mk_contract(task_id="T-2", objective="add a logout button")]

    def test_null_backend_no_rescore(self):
        self.assertFalse(Scorer(None).rescore(self.contracts, "req"))

    def test_llm_rescore_overwrites(self):
        payload = json.dumps({"T-1": {a: 70 for a in
                              ("complexity", "ambiguity", "risk", "context_size", "tool_risk", "domain_specialization")},
                              "T-2": {a: 10 for a in
                              ("complexity", "ambiguity", "risk", "context_size", "tool_risk", "domain_specialization")}})
        ok = Scorer(FixedBackend(payload)).rescore(self.contracts, "req")
        self.assertTrue(ok)
        self.assertEqual(self.contracts[0].scores.complexity, 70)
        self.assertEqual(self.contracts[1].scores.risk, 10)

    def test_garbage_falls_back(self):
        before = self.contracts[0].scores.complexity
        sc = Scorer(FixedBackend("sorry no"))
        self.assertFalse(sc.rescore(self.contracts, "req"))
        self.assertIsNotNone(sc.last_error)
        self.assertEqual(self.contracts[0].scores.complexity, before)  # unchanged

    def test_missing_task_id_falls_back(self):
        payload = json.dumps({"T-1": {a: 5 for a in
                              ("complexity", "ambiguity", "risk", "context_size", "tool_risk", "domain_specialization")}})
        self.assertFalse(Scorer(FixedBackend(payload)).rescore(self.contracts, "req"))


class TestOrchestratorLLMScore(unittest.TestCase):
    def test_template_path_rescored_by_backend(self):
        orch = Orchestrator.default(SmartBackend())
        plan = orch.plan("READMEの誤字を修正する")  # classifies to a template, LLM decompose fails
        self.assertTrue(plan.decomposition_strategy.endswith(":template+llmscore"))
        # every score axis came back as 50 from the SmartBackend
        self.assertTrue(all(c.scores.complexity == 50 for c in plan.contracts))


# --------------------------------------------------------------------------- #
class TestReviewerHeuristic(unittest.TestCase):
    def setUp(self):
        self.rv = Reviewer(None)  # no backend -> deterministic

    def _one(self, lens, contract):
        return self.rv._heuristic_review(contract, lens, None)

    def test_auto_check_pass(self):
        self.assertEqual(self._one(ReviewLens.AUTO_CHECK, mk_contract()).verdict, "pass")

    def test_requirements_single_criterion_concerns(self):
        c = mk_contract(acceptance_criteria=["only one"])
        self.assertEqual(self._one(ReviewLens.REQUIREMENTS, c).verdict, "concerns")

    def test_edge_cases_without_boundary_concerns(self):
        c = mk_contract(acceptance_criteria=["does the happy path"])
        self.assertEqual(self._one(ReviewLens.EDGE_CASES, c).verdict, "concerns")

    def test_edge_cases_with_boundary_pass(self):
        c = mk_contract(acceptance_criteria=["rejects empty input", "handles boundary at limit"])
        self.assertEqual(self._one(ReviewLens.EDGE_CASES, c).verdict, "pass")

    def test_security_hazard_without_constraint_concerns(self):
        c = mk_contract(objective="rotate the production secret token", constraints=[])
        r = self._one(ReviewLens.SECURITY, c)
        self.assertEqual(r.verdict, "concerns")
        self.assertEqual(r.severity, "medium")

    def test_security_hazard_with_secret_constraint_pass(self):
        c = mk_contract(objective="rotate the production secret token",
                        constraints=["秘密情報をログに出力しない"])
        self.assertEqual(self._one(ReviewLens.SECURITY, c).verdict, "pass")

    def test_run_one_result_per_step(self):
        c = mk_contract()
        plan = ReviewPlan(task_id=c.task_id, steps=[
            ReviewStep(ReviewLens.AUTO_CHECK, "AutoChecker", ModelTier.SMALL),
            ReviewStep(ReviewLens.REQUIREMENTS, "RequirementsReviewer", ModelTier.SMALL),
        ])
        self.assertEqual(len(self.rv.run(c, plan)), 2)


class TestReviewerLLM(unittest.TestCase):
    def test_llm_review_parsed(self):
        rv = Reviewer(FixedBackend('{"verdict":"fail","severity":"high","rationale":"bad","findings":["x"]}'))
        r = rv._llm_review(mk_contract(), ReviewLens.SECURITY, None)
        self.assertEqual(r.verdict, "fail")
        self.assertEqual(r.severity, "high")

    def test_bad_verdict_falls_back_to_heuristic(self):
        rv = Reviewer(FixedBackend('{"verdict":"maybe"}'))
        c = mk_contract()
        plan = ReviewPlan(task_id=c.task_id,
                          steps=[ReviewStep(ReviewLens.AUTO_CHECK, "AutoChecker", ModelTier.SMALL)])
        results = rv.run(c, plan)            # _llm_review raises -> heuristic
        self.assertEqual(results[0].verdict, "pass")
        self.assertIsNotNone(rv.last_error)


class TestReviewAndJudge(unittest.TestCase):
    def test_clean_contract_accepts(self):
        orch = Orchestrator.default("null")
        c = mk_contract(acceptance_criteria=["rejects empty input", "handles boundary at limit"])
        _, results, decision = orch.review_and_judge(c)
        self.assertTrue(results)
        self.assertEqual(decision.decision, JudgeDecision.ACCEPT)

    def test_security_fail_drives_judge(self):
        # An LLM reviewer reports a critical security failure -> Judge discards.
        orch = Orchestrator.default(
            FixedBackend('{"verdict":"fail","severity":"critical","rationale":"secret leak","findings":["logs token"]}'))
        c = mk_contract(objective="本番にデプロイして秘密鍵を更新する", task_type=TaskType.CODE_IMPLEMENTATION)
        _, results, decision = orch.review_and_judge(c, artifact="console.log(token)")
        self.assertIn(decision.decision, (JudgeDecision.DISCARD, JudgeDecision.REVISE))


if __name__ == "__main__":
    unittest.main()
