import unittest

from tehai.registry import AgentRegistry
from tehai.schema import load_schema, validate
from tehai.teams import (
    AutonomousLoopGuard, AutonomyLevel, FailureRouter, FailureSignal, FailureType,
    LoopGuardConfig, LoopState, MetaOrchestrator, TeamContract, TeamRegistry,
    TeamTaskStatus,
)


class TestTeamRegistry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = TeamRegistry.load()
        cls.agents = AgentRegistry.load()

    def test_seven_teams(self):
        for tid in ("product_planning_team", "architecture_team", "implementation_team",
                    "verification_team", "security_team", "integration_team", "documentation_team"):
            self.assertIn(tid, self.reg)

    def test_phase_roles_are_registered_agents(self):
        for team in self.reg.all():
            for p in team.phases:
                self.assertIn(p["role"], self.agents, f"{team.team_id}:{p['role']}")

    def test_team_phases_dont_violate_own_forbidden(self):
        for team in self.reg.all():
            for p in team.phases:
                self.assertNotIn(p["task_type"], team.forbidden_task_types,
                                 f"{team.team_id} phase {p['key']} is self-forbidden")

    def test_forbids_mechanism(self):
        impl = self.reg.get("implementation_team")
        self.assertTrue(self.reg.forbids(impl, "production_deploy"))
        self.assertFalse(self.reg.forbids(impl, "code_implementation"))

    def test_to_org_template(self):
        org = self.reg.to_org_template(self.reg.get("implementation_team"))
        self.assertGreaterEqual(len(org.phases), 3)


class TestTeamContract(unittest.TestCase):
    def _c(self, **over):
        base = dict(team_task_id="TT-1", assigned_team="implementation_team",
                    objective="認証ログイン処理を実装する", expected_outputs=["auth.ts"],
                    acceptance_criteria=["正しい資格情報でログインできる"])
        base.update(over)
        return TeamContract(**base)

    def test_valid(self):
        self.assertEqual(self._c().validate(), [])

    def test_vague_rejected(self):
        self.assertTrue(self._c(objective="全部よしなに実装する").validate())

    def test_missing_outputs(self):
        self.assertTrue(self._c(expected_outputs=[]).validate())

    def test_schema_conformant(self):
        schema = load_schema("team_contract.schema.json")
        self.assertEqual(validate(self._c().to_dict(), schema), [])


class TestFailureRouter(unittest.TestCase):
    def setUp(self):
        self.r = FailureRouter()

    def test_every_type_routes(self):
        expected = {
            FailureType.REQUIREMENT_AMBIGUITY: "product_planning_team",
            FailureType.ARCHITECTURE_CONFLICT: "architecture_team",
            FailureType.IMPLEMENTATION_ERROR: "implementation_team",
            FailureType.TEST_FAILURE: "implementation_team",
            FailureType.SECURITY_RISK: "security_team",
            FailureType.INTEGRATION_CONFLICT: "integration_team",
            FailureType.COST_OVERRUN: "meta",
            FailureType.CONTEXT_OVERFLOW: "architecture_team",
            FailureType.PERMISSION_VIOLATION: "human",
            FailureType.REPEATED_FAILURE: "human",
            FailureType.UNKNOWN_FAILURE: "meta",
        }
        for ft, route in expected.items():
            self.assertEqual(self.r.route(ft)[0], route, ft)

    def test_security_requires_human(self):
        self.assertTrue(self.r.route(FailureType.SECURITY_RISK)[3])

    def test_classify_infers_security(self):
        f = self.r.classify(FailureSignal(task_id="T", detected_by="x", security_finding=True))
        self.assertEqual(f.failure_type, FailureType.SECURITY_RISK)

    def test_classify_infers_repeated(self):
        f = self.r.classify(FailureSignal(task_id="T", detected_by="x", judge_discard=True), counter=3)
        self.assertEqual(f.failure_type, FailureType.REPEATED_FAILURE)


class TestLoopGuard(unittest.TestCase):
    def setUp(self):
        self.g = AutonomousLoopGuard(LoopGuardConfig(max_loops=6, max_same_failure=3))
        self.r = FailureRouter()

    def _f(self, ft):
        return self.r.classify(FailureSignal(task_id="T", detected_by="x", failure_type=ft))

    def test_low_risk_allows_autorun(self):
        d = self.g.evaluate(self._f(FailureType.IMPLEMENTATION_ERROR), LoopState(), same_failure_count=1)
        self.assertTrue(d.allow_autorun)

    def test_security_requires_human(self):
        d = self.g.evaluate(self._f(FailureType.SECURITY_RISK), LoopState())
        self.assertFalse(d.allow_autorun)
        self.assertEqual(d.stop.value, "request_human_approval")

    def test_repeated_stops(self):
        d = self.g.evaluate(self._f(FailureType.IMPLEMENTATION_ERROR), LoopState(), same_failure_count=3)
        self.assertFalse(d.allow_autorun)
        self.assertEqual(d.stop.value, "stop_as_failed")

    def test_cost_overrun_shrinks(self):
        d = self.g.evaluate(self._f(FailureType.COST_OVERRUN), LoopState(), same_failure_count=1)
        self.assertEqual(d.stop.value, "shrink_scope")

    def test_manual_blocks(self):
        d = self.g.evaluate(self._f(FailureType.IMPLEMENTATION_ERROR), LoopState(),
                            autonomy=AutonomyLevel.MANUAL)
        self.assertFalse(d.allow_autorun)

    def test_max_loops(self):
        st = LoopState(loop_count=6)
        d = self.g.evaluate(self._f(FailureType.IMPLEMENTATION_ERROR), st, same_failure_count=1)
        self.assertEqual(d.stop.value, "defer_to_backlog")


class TestMetaOrchestrator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = MetaOrchestrator.default()

    def test_composition_readme_doc_only(self):
        self.assertEqual(self.m.select_team_composition("READMEの誤字を修正する"), ["documentation_team"])

    def test_composition_feature_full(self):
        c = self.m.select_team_composition("Todoに完了フラグ機能を実装する")
        self.assertGreaterEqual(len(c), 5)
        self.assertNotIn("security_team", c)

    def test_composition_auth_includes_security(self):
        self.assertIn("security_team", self.m.select_team_composition("認証機能を実装する"))

    def test_run_normal_completes(self):
        r = self.m.run("Todoに完了フラグ機能を実装する")
        self.assertEqual(r.final_status, "completed")
        self.assertGreaterEqual(len(r.team_contracts), 2)
        self.assertTrue(all(c.is_valid for c in r.team_contracts))
        self.assertGreater(r.metrics["task_count"], 0)

    def test_team_contracts_form_dag(self):
        r = self.m.run("機能を実装する")
        ids = {c.team_task_id for c in r.team_contracts}
        self.assertEqual(len(r.execution_order), len(r.team_contracts))
        pos = {t: i for i, t in enumerate(r.execution_order)}
        for c in r.team_contracts:
            for dep in c.dependencies:
                self.assertIn(dep, ids)
                self.assertLess(pos[dep], pos[c.team_task_id])

    def test_requirement_ambiguity_routes_and_recovers(self):
        r = self.m.run("機能Xを実装する",
                       injected_failures={"implementation_team": (FailureType.REQUIREMENT_AMBIGUITY, 1)})
        routes = [h["failure"]["recommended_route"] for h in r.loop_history]
        self.assertIn("product_planning_team", routes)
        self.assertTrue(r.clarification_reports)
        self.assertEqual(r.final_status, "completed")
        self.assertFalse(r.human_intervention_required)

    def test_security_risk_requires_human(self):
        r = self.m.run("認証トークンを外部送信する機能を実装する",
                       injected_failures={"security_team": (FailureType.SECURITY_RISK, 9)})
        self.assertTrue(r.human_intervention_required)
        self.assertEqual(r.final_status, "request_human_approval")

    def test_repeated_failure_stops(self):
        r = self.m.run("機能Yを実装する",
                       injected_failures={"implementation_team": (FailureType.IMPLEMENTATION_ERROR, 9)})
        self.assertIn(r.final_status, ("stop_as_failed", "request_human_approval"))
        self.assertLessEqual(r.metrics["loop_count"], 4)

    def test_integration_conflict_routes_to_integration_not_impl(self):
        r = self.m.run("複数モジュールを統合する機能を実装する",
                       injected_failures={"integration_team": (FailureType.INTEGRATION_CONFLICT, 1)})
        routes = [h["failure"]["recommended_route"] for h in r.loop_history]
        self.assertIn("integration_team", routes)
        self.assertNotIn("implementation_team", routes)

    def test_empty_goal_rejected(self):
        with self.assertRaises(ValueError):
            self.m.run("   ")

    def test_hazardous_goal_triggers_competition(self):
        r = self.m.run("認証トークンを安全に発行する機能を実装する")
        self.assertTrue(r.competitions)
        self.assertEqual(r.competitions[0]["winner_priority"], "security")

    def test_compete_flag_forces_competition(self):
        r = self.m.run("Todoの表示機能を実装する", compete=True)
        self.assertTrue(r.competitions)
        self.assertEqual(r.competitions[0]["winner_priority"], "maintainability")

    def test_no_competition_for_low_risk_default(self):
        r = self.m.run("Todoの表示機能を実装する")
        self.assertEqual(r.competitions, [])

    def test_cascading_reroute_reruns_downstream(self):
        # requirement_ambiguity on architecture -> reroute to product_planning ->
        # downstream (architecture, implementation, ...) re-run -> recover.
        r = self.m.run("データ同期機能を実装する",
                       injected_failures={"architecture_team": (FailureType.REQUIREMENT_AMBIGUITY, 1)})
        self.assertEqual(r.final_status, "completed")
        # the rerouted (planning) and a downstream team both carry loop_count > 0
        by_team = {c.assigned_team: c for c in r.team_contracts}
        self.assertGreater(by_team["product_planning_team"].loop_count, 0)


class TestCompetition(unittest.TestCase):
    def setUp(self):
        from tehai.teams import CrossTeamCompetition
        self.c = CrossTeamCompetition()

    def test_security_goal_security_wins(self):
        r = self.c.run("auth", "秘密鍵を扱う認証設計")
        self.assertEqual(r.winner_priority, "security")
        self.assertIn(r.winner, [a.approach_id for a in r.approaches])

    def test_normal_goal_maintainability_wins(self):
        r = self.c.run("ui", "リスト表示の設計")
        self.assertEqual(r.winner_priority, "maintainability")

    def test_eight_criteria_scored(self):
        from tehai.teams.competition import CRITERIA
        r = self.c.run("x", "設計")
        self.assertEqual(set(r.approaches[0].scores), set(CRITERIA))
        self.assertEqual(len(CRITERIA), 8)


if __name__ == "__main__":
    unittest.main()
