import unittest

from tehai.models import Action, AgentTemplate, ModelTier
from tehai.permissions import ActionDecision, PermissionModel


def agent(aid, allowed, forbidden=()):
    return AgentTemplate(
        agent_template_id=aid, role=aid, mission="m",
        allowed_actions=list(allowed), forbidden_actions=list(forbidden),
        recommended_model_tier=ModelTier.MEDIUM,
    )


class TestPermissions(unittest.TestCase):
    def setUp(self):
        self.pm = PermissionModel()

    def test_allowed_safe_action(self):
        a = agent("Reader", ["read_file"])
        self.assertEqual(self.pm.check_action(a, Action.READ_FILE), ActionDecision.ALLOWED)

    def test_not_granted_is_forbidden(self):
        a = agent("Reader", ["read_file"])
        self.assertEqual(self.pm.check_action(a, Action.WRITE_FILE), ActionDecision.FORBIDDEN)

    def test_explicit_forbidden(self):
        a = agent("Rev", ["read_repository"], ["write_file"])
        self.assertEqual(self.pm.check_action(a, Action.WRITE_FILE), ActionDecision.FORBIDDEN)

    def test_dangerous_granted_needs_approval(self):
        a = agent("Releaser", ["production_deploy"])
        self.assertEqual(self.pm.check_action(a, Action.PRODUCTION_DEPLOY), ActionDecision.NEEDS_APPROVAL)
        req = self.pm.request_approval(a, Action.PRODUCTION_DEPLOY, "ship v2")
        self.assertTrue(req.dry_run_available)
        self.assertEqual(len(self.pm.pending_approvals), 1)

    def test_child_subset_ok(self):
        parent = agent("Lead", ["read_file", "write_file", "run_test"])
        child = agent("Eng", ["read_file", "write_file"])
        self.assertTrue(self.pm.enforce_child_subset(parent, child).ok)

    def test_child_exceeds_parent(self):
        parent = agent("Lead", ["read_file", "write_file"])
        child = agent("Eng", ["read_file", "git_push"])
        chk = self.pm.enforce_child_subset(parent, child)
        self.assertFalse(chk.ok)
        self.assertIn(Action.GIT_PUSH, chk.violating_actions)


if __name__ == "__main__":
    unittest.main()
