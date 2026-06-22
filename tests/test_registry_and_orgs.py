import unittest

from tehai.models import AgentTemplate, ModelTier, TaskType
from tehai.registry import AgentRegistry, RegistryError
from tehai.org_templates import CATALOG, select_org_template
from tehai.schema import validate_agent_template


class TestRegistry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = AgentRegistry.load()

    def test_loads_core_templates(self):
        for tid in ("ProductManager", "SecurityReviewer", "FrontendEngineer", "Judge"):
            self.assertIn(tid, self.reg)

    def test_all_templates_schema_valid(self):
        for t in self.reg.all():
            # Reconstruct the on-disk dict shape and validate.
            d = {
                "agent_template_id": t.agent_template_id, "role": t.role, "mission": t.mission,
                "responsibilities": t.responsibilities, "allowed_actions": t.allowed_actions,
                "forbidden_actions": t.forbidden_actions, "required_context": t.required_context,
                "output_schema": t.output_schema, "quality_bar": t.quality_bar,
                "escalation_rules": t.escalation_rules,
                "recommended_model_tier": t.recommended_model_tier.value,
                "handles_task_types": t.handles_task_types,
            }
            self.assertEqual(validate_agent_template(d), [], t.agent_template_id)

    def test_effort_derived_from_tier(self):
        from tehai.models import Effort
        self.assertEqual(self.reg.get("SecurityReviewer").recommended_effort, Effort.HIGH)   # large
        self.assertEqual(self.reg.get("AutoChecker").recommended_effort, Effort.LOW)         # small
        self.assertEqual(self.reg.get("BackendEngineer").recommended_effort, Effort.MEDIUM)  # medium

    def test_select_for_task_type(self):
        t = self.reg.select_for_task(TaskType.SECURITY_REVIEW)
        self.assertIn("security_review", t.handles_task_types)

    def test_preferred_honored(self):
        t = self.reg.select_for_task(TaskType.CODE_IMPLEMENTATION, preferred_id="FrontendEngineer")
        self.assertEqual(t.agent_template_id, "FrontendEngineer")

    def test_propose_records_not_creates(self):
        before = len(self.reg.ids())
        self.reg.propose_new_template("NovelRole", "no fit", TaskType.GENERIC)
        self.assertEqual(len(self.reg.ids()), before)
        self.assertTrue(self.reg.proposals)

    def test_from_dict_detects_conflict(self):
        t = AgentTemplate.from_dict({
            "agent_template_id": "X", "role": "X", "mission": "m",
            "allowed_actions": ["write_file"], "forbidden_actions": ["write_file"],
            "recommended_model_tier": "small",
        })
        self.assertTrue(any("both allowed and forbidden" in e for e in t.errors()))


class TestOrgTemplates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = AgentRegistry.load()

    def test_keyword_classification(self):
        cases = {
            "認証フローの脆弱性を監査する": "security_review",
            "本番障害のインシデント対応": "incident_response",
            "競合の文献を調査して比較する": "research",
            "新機能の記事を執筆する": "content_production",
            "APIのバグを実装で修正する": "code_implementation",
        }
        for req, expected in cases.items():
            self.assertEqual(select_org_template(req).org_template_id, expected, req)

    def test_default_is_product_delivery(self):
        self.assertEqual(select_org_template("xyzzy frobnicate").org_template_id, "product_delivery")

    def test_trivial_request_collapses_to_single_deliverable(self):
        org = select_org_template("READMEの誤字を修正する")
        self.assertEqual(org.org_template_id, "single_deliverable")
        self.assertEqual(len(org.phases), 1)
        self.assertIn(org.phases[0].role, self.reg)  # role is a registered agent

    def test_nontrivial_stays_multiphase(self):
        self.assertGreater(len(select_org_template("認証フローの脆弱性を監査する").phases), 1)

    def test_org_roles_exist_in_registry(self):
        for org in CATALOG.values():
            for phase in org.phases:
                self.assertIn(phase.role, self.reg, f"{org.org_template_id}:{phase.role}")

    def test_org_phase_deps_reference_known_keys(self):
        for org in CATALOG.values():
            keys = {p.key for p in org.phases}
            for p in org.phases:
                for d in p.depends_on:
                    self.assertIn(d, keys, f"{org.org_template_id}:{p.key}->{d}")


if __name__ == "__main__":
    unittest.main()
