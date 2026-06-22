import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from tehai.decompose_guard import DecompositionGuard, DecompositionState, GuardConfig
from tehai.executor import Executor
from tehai.judge import Judge
from tehai.model_router import ModelRouter
from tehai.models import JudgeDecision, ReviewLens, TaskStatus
from tehai.permissions import PermissionModel
from tehai.registry import AgentRegistry
from tehai.review_planner import ReviewPlanner
from tehai.reviewer import Reviewer
from tehai.sandbox import Sandbox, SandboxResult, _safe_name
from tehai.backends import ModelBackend
from _util import mk_contract


GOOD_PY = "def add(a, b):\n    return a + b\n"
GOOD_TEST = ("import unittest\nfrom calc import add\n\n"
             "class T(unittest.TestCase):\n"
             "    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n\n"
             "if __name__ == '__main__':\n    unittest.main()\n")
FAIL_TEST = GOOD_TEST.replace("add(1, 2), 3", "add(1, 2), 99")


class Fixed(ModelBackend):
    name = "fixed"
    available = True

    def __init__(self, payload):
        self.payload = payload

    def complete(self, prompt, tier, **kw):
        return self.payload


class TestSandboxUnit(unittest.TestCase):
    def test_compiles_good_python(self):
        r = Sandbox().run({"calc.py": GOOD_PY})
        self.assertTrue(r.ran)
        self.assertTrue(r.passed, r.stderr)

    def test_syntax_error_fails(self):
        r = Sandbox().run({"bad.py": "def add(a, b)\n    return a+b\n"})
        self.assertTrue(r.ran)
        self.assertFalse(r.passed)
        self.assertIn("py_compile", r.runner)

    def test_passing_test_runs_green(self):
        r = Sandbox().run({"calc.py": GOOD_PY, "test_calc.py": GOOD_TEST})
        self.assertTrue(r.passed, r.stderr or r.stdout)

    def test_failing_test_is_red(self):
        r = Sandbox().run({"calc.py": GOOD_PY, "test_calc.py": FAIL_TEST})
        self.assertTrue(r.ran)
        self.assertFalse(r.passed)

    def test_timeout_killed(self):
        # impl sibling present so the test file is actually executed (then loops)
        r = Sandbox(timeout=2).run({"impl.py": "VALUE = 1\n",
                                    "test_loop.py": "while True:\n    pass\n"})
        self.assertTrue(r.ran)
        self.assertFalse(r.passed)
        self.assertTrue(r.timed_out)

    def test_no_runner_for_unknown_language(self):
        r = Sandbox().run({"thing.xyz": "whatever"})
        self.assertFalse(r.ran)
        self.assertIn("no runner", r.reason)

    def test_prose_named_python_still_compiles(self):
        # the model named the artifact with a description, not a .py filename
        r = Sandbox().run({"Python file containing the implementation": GOOD_PY})
        self.assertTrue(r.ran, "content sniffing should recover a .py runner")
        self.assertTrue(r.passed, r.stderr)
        self.assertTrue(any(f.endswith(".py") for f in r.files))

    def test_lone_test_is_compile_only_not_executed(self):
        # a test importing a missing sibling, ALONE -> compiled (ok), never executed
        body = ("import unittest\nfrom missing_module import thing\n"
                "class T(unittest.TestCase):\n    def test(self): self.assertTrue(thing)\n")
        r = Sandbox().run({"test_thing.py": body})
        self.assertTrue(r.ran)
        self.assertTrue(r.passed)  # only py_compile ran; the failing import never executed
        self.assertIn("py_compile", r.runner)

    def test_isolation_reported(self):
        r = Sandbox().run({"calc.py": GOOD_PY})
        self.assertIn(r.isolation, ("unshare", "none"))

    def test_isolation_none_mode(self):
        sb = Sandbox(isolation="none")
        self.assertEqual(sb.resolve_isolation()[0], "none")
        r = sb.run({"calc.py": GOOD_PY})
        self.assertEqual(r.isolation, "none")
        self.assertTrue(r.passed)

    def test_strict_resolves_unshare_or_refuse(self):
        self.assertIn(Sandbox(isolation="strict").resolve_isolation()[0], ("unshare", "refuse"))

    def test_strict_refuses_when_isolation_unavailable(self):
        sb = Sandbox(isolation="strict")
        sb._unshare_works = lambda: False  # simulate no OS isolation
        r = sb.run({"calc.py": GOOD_PY})
        self.assertFalse(r.ran)
        self.assertEqual(r.isolation, "refuse")
        self.assertIn("strict isolation", r.reason)

    def test_strict_runs_when_isolation_available(self):
        sb = Sandbox(isolation="strict")
        if sb.resolve_isolation()[0] != "unshare":
            self.skipTest("no unshare available")
        r = sb.run({"calc.py": GOOD_PY})
        self.assertTrue(r.passed, r.stderr)
        self.assertEqual(r.isolation, "unshare")

    def test_network_blocked_when_isolated(self):
        sb = Sandbox()
        if sb.resolve_isolation()[0] != "unshare":
            self.skipTest("no OS isolation (unshare) available in this environment")
        net = ("import socket, sys\n"
               "try:\n"
               "    socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
               "    sys.exit(1)\n"      # reachable -> isolation failed
               "except OSError:\n"
               "    sys.exit(0)\n")     # blocked -> good
        r = sb.run({"test_net.py": net})
        self.assertEqual(r.isolation, "unshare")
        self.assertTrue(r.passed, f"network should be blocked: {r.stderr or r.stdout}")

    def test_home_secret_read_blocked_when_isolated(self):
        from pathlib import Path
        sb = Sandbox()
        if sb.resolve_isolation()[0] != "unshare":
            self.skipTest("no OS isolation (unshare) available")
        secret = Path.home() / f"tehai_test_secret_{os.getpid()}"
        secret.write_text("TOPSECRET", encoding="utf-8")
        try:
            body = ("import sys\n"
                    f"try:\n    open({str(secret)!r}).read(); sys.exit(1)\n"
                    "except OSError:\n    sys.exit(0)\n")
            r = sb.run({"test_read.py": body})
            self.assertTrue(r.passed, "a $HOME secret should be unreadable under FS hardening")
        finally:
            secret.unlink(missing_ok=True)

    def test_filename_sanitized(self):
        self.assertEqual(_safe_name("../../etc/passwd"), "passwd")
        self.assertEqual(_safe_name("a b.py"), "a_b.py")
        r = Sandbox().run({"../evil.py": GOOD_PY})
        self.assertEqual(r.files, ["evil.py"])  # never escapes the temp dir


def build_executor(backend, sandbox, reviewer=None, guard=None):
    return Executor(
        AgentRegistry.load(), reviewer or Reviewer(None), Judge(), ReviewPlanner(),
        ModelRouter(), PermissionModel(), guard or DecompositionGuard(), backend,
        sandbox=sandbox,
    )


class TestExecutorWithSandbox(unittest.TestCase):
    def _contract(self):
        return mk_contract(objective="implement an add function",
                           expected_output=["calc.py", "test_calc.py"],
                           acceptance_criteria=["adds two numbers", "handles the boundary at zero"])

    def test_passing_code_grounds_accept(self):
        be = Fixed(json.dumps({"calc.py": GOOD_PY, "test_calc.py": GOOD_TEST}))
        ex = build_executor(be, Sandbox())
        r = ex.execute_task(self._contract(), {}, DecompositionState())
        self.assertEqual(r.status, TaskStatus.COMPLETED)
        # the auto_check verdict came from real execution
        auto = [rv for rv in r.reviews if rv.lens == ReviewLens.AUTO_CHECK]
        self.assertTrue(auto and auto[0].verdict == "pass")
        self.assertTrue(any("sandbox" in t and "pass" in t for t in r.trace))

    def test_broken_code_drives_revise_then_escalate(self):
        be = Fixed(json.dumps({"calc.py": "def add(a, b)\n    return a+b\n"}))  # syntax error
        ex = build_executor(be, Sandbox(), reviewer=Reviewer(None),
                            guard=DecompositionGuard(GuardConfig(max_retries=1)))
        r = ex.execute_task(self._contract(), {}, DecompositionState())
        self.assertIn(r.status, (TaskStatus.ESCALATED, TaskStatus.FAILED))
        auto = [rv for rv in r.reviews if rv.lens == ReviewLens.AUTO_CHECK]
        self.assertTrue(auto and auto[0].verdict == "fail")
        self.assertTrue(any("FAIL" in t for t in r.trace))


if __name__ == "__main__":
    unittest.main()
