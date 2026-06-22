import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from tehai.architect import TaskArchitect, _extract_json, LLMDecompositionError
from tehai.backends import (
    EchoBackend, ModelBackend, NullBackend, OllamaBackend, get_backend,
)
from tehai.models import ModelTier, TaskType
from tehai.org_templates import CATALOG
from tehai.orchestrator import Orchestrator
from tehai.schema import validate_task_contract


# --- deterministic fake backends (no network/subprocess) --------------------- #
class FakeBackend(ModelBackend):
    name = "fake"
    available = True

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete(self, prompt, tier, **kw):
        self.calls += 1
        return self.payload


GOOD = json.dumps([
    {"id": "a", "objective": "implement the email format validator",
     "task_type": "code_implementation", "role": "BackendEngineer",
     "expected_output": ["validator.ts"], "acceptance_criteria": ["rejects malformed email"],
     "dependencies": [], "scores": {"complexity": 30, "ambiguity": 20, "risk": 15,
                                    "context_size": 10, "tool_risk": 10, "domain_specialization": 20}},
    {"id": "b", "objective": "write boundary tests for the validator",
     "task_type": "test_authoring", "role": "TestEngineer",
     "expected_output": ["validator.test.ts"], "acceptance_criteria": ["covers empty + overlong"],
     "dependencies": ["a"]},
])


class TestExtractJSON(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(_extract_json('[{"x":1}]'), [{"x": 1}])

    def test_fenced(self):
        self.assertEqual(_extract_json('```json\n[{"x":1}]\n```'), [{"x": 1}])

    def test_with_prose(self):
        self.assertEqual(_extract_json('Here you go:\n[1, 2, 3]\nDone.'), [1, 2, 3])

    def test_no_json_raises(self):
        with self.assertRaises(LLMDecompositionError):
            _extract_json("sorry, I cannot help")


class TestBackendsBasic(unittest.TestCase):
    def test_get_backend_null_default(self):
        self.assertIsInstance(get_backend(), NullBackend)

    def test_null_raises(self):
        with self.assertRaises(RuntimeError):
            get_backend("null").complete("x", ModelTier.SMALL)

    def test_echo_roundtrips(self):
        out = get_backend("echo").complete("hello", ModelTier.MEDIUM)
        self.assertIn("medium", out)

    def test_unknown_backend(self):
        with self.assertRaises(ValueError):
            get_backend("nope")

    def test_ollama_host_override(self):
        be = get_backend("ollama", host="http://example:1234")
        self.assertEqual(be.host, "http://example:1234")
        self.assertIsInstance(be, OllamaBackend)

    def test_ollama_ssh_transport(self):
        # Mac Studio "strong floor" reached over SSH (no open HTTP port).
        be = get_backend("ollama", transport="ssh", ssh_host="mac-studio",
                         model_by_tier={ModelTier.LARGE: "qwen2.5:72b"})
        self.assertEqual(be.transport, "ssh")
        self.assertEqual(be.ssh_host, "mac-studio")
        cmd = be._ssh_cmd("qwen2.5:72b")
        self.assertEqual(cmd[0], "ssh")
        self.assertIn("mac-studio", cmd)
        self.assertEqual(cmd[-3:], ["ollama", "run", "qwen2.5:72b"])

    def test_ssh_openai_cmd(self):
        # OpenAI-compatible server (e.g. Mac Studio MLX) called over SSH + curl.
        be = get_backend("ssh-openai", ssh_host="user@host", port=8080,
                         model_by_tier={ModelTier.LARGE: "some/model"})
        self.assertEqual(be.name, "ssh-openai")
        cmd = be._ssh_cmd()
        self.assertEqual(cmd[0], "ssh")
        self.assertIn("user@host", cmd)
        self.assertIn("curl", cmd[-1])
        self.assertIn("localhost:8080/v1/chat/completions", cmd[-1])


class TestLLMDecompose(unittest.TestCase):
    def setUp(self):
        self.org = CATALOG["code_implementation"]

    def test_happy_llm_path(self):
        arch = TaskArchitect(backend=FakeBackend(GOOD))
        contracts, _ = arch.decompose("validate email", self.org, "R-x")
        self.assertEqual(arch.last_strategy, "llm")
        self.assertEqual(len(contracts), 2)
        ids = {c.task_id for c in contracts}
        self.assertEqual(ids, {"R-x-L000", "R-x-L001"})
        for c in contracts:
            self.assertEqual(c.validate(), [])
            self.assertEqual(validate_task_contract(c.to_dict()), [])
        # dependency remapped from "a" -> R-x-L000
        dep_child = next(c for c in contracts if c.task_id == "R-x-L001")
        self.assertEqual(dep_child.dependencies, ["R-x-L000"])

    def test_orchestrator_uses_llm_and_labels_strategy(self):
        orch = Orchestrator.default(FakeBackend(GOOD))
        plan = orch.plan("validate email")
        self.assertTrue(plan.decomposition_strategy.endswith(":llm"))
        self.assertGreaterEqual(len(plan.contracts), 2)
        for c in plan.contracts:
            self.assertIn(c.assigned_agent_template, orch.registry)

    def _fallback(self, payload):
        arch = TaskArchitect(backend=FakeBackend(payload))
        contracts, _ = arch.decompose("validate email", self.org, "R-x")
        self.assertEqual(arch.last_strategy, "template")
        self.assertIsNotNone(arch.last_error)
        self.assertTrue(contracts)  # template path still produced a plan
        return arch

    def test_fallback_on_garbage(self):
        self._fallback("I cannot do that")

    def test_fallback_on_vague_objective(self):
        bad = json.dumps([{"id": "a", "objective": "全部よしなにやる",
                           "task_type": "code_implementation", "expected_output": ["x"],
                           "acceptance_criteria": ["y"]}])
        self._fallback(bad)

    def test_fallback_on_missing_expected_output(self):
        bad = json.dumps([{"id": "a", "objective": "do the real concrete thing",
                           "task_type": "code_implementation", "expected_output": [],
                           "acceptance_criteria": ["y"]}])
        self._fallback(bad)

    def test_fallback_on_duplicate_objective(self):
        bad = json.dumps([
            {"id": "a", "objective": "same thing", "task_type": "generic",
             "expected_output": ["x"], "acceptance_criteria": ["y"]},
            {"id": "b", "objective": "same thing", "task_type": "generic",
             "expected_output": ["x"], "acceptance_criteria": ["y"]},
        ])
        self._fallback(bad)

    def test_fallback_on_cycle(self):
        bad = json.dumps([
            {"id": "a", "objective": "first concrete task", "task_type": "generic",
             "expected_output": ["x"], "acceptance_criteria": ["y"], "dependencies": ["b"]},
            {"id": "b", "objective": "second concrete task", "task_type": "generic",
             "expected_output": ["x"], "acceptance_criteria": ["y"], "dependencies": ["a"]},
        ])
        self._fallback(bad)

    def test_fallback_on_too_many(self):
        big = [{"id": f"t{i}", "objective": f"concrete subtask number {i}",
                "task_type": "generic", "expected_output": ["x"],
                "acceptance_criteria": ["y"]} for i in range(25)]
        self._fallback(json.dumps(big))

    def test_null_list_fields_coerced(self):
        # Regression: a real model returned `"dependencies": null` etc.; must not crash.
        payload = json.dumps([{
            "id": "a", "objective": "implement a concrete email validator",
            "task_type": "code_implementation", "role": "BackendEngineer",
            "expected_output": ["validator.ts"], "acceptance_criteria": ["rejects bad email"],
            "dependencies": None, "constraints": None, "scores": None,
        }])
        arch = TaskArchitect(backend=FakeBackend(payload))
        contracts, _ = arch.decompose("validate email", self.org, "R-x")
        self.assertEqual(arch.last_strategy, "llm")
        self.assertEqual(contracts[0].validate(), [])

    def test_null_backend_uses_template(self):
        orch = Orchestrator.default("null")
        plan = orch.plan("validate email")
        self.assertTrue(plan.decomposition_strategy.endswith(":template"))


if __name__ == "__main__":
    unittest.main()
