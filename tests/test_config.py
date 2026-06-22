import json
import tempfile
import unittest
from pathlib import Path

from tehai.config import DEFAULTS, load_config, router_from_config
from tehai.evaluation import EvaluationStore
from tehai.model_router import MEDIUM_MAX, SMALL_MAX
from tehai.orchestrator import Orchestrator


def rec(model, decision="accept"):
    return {"run_id": "R", "task_id": "T", "task_type": "code_implementation",
            "selected_model": model, "judge_decision": decision}


class TestConfig(unittest.TestCase):
    def test_none_is_defaults(self):
        self.assertEqual(load_config(None), DEFAULTS)

    def test_known_keys_applied_unknown_ignored(self):
        cfg = load_config({"router_small_max": 12, "router_medium_max": 40, "junk": 99})
        self.assertEqual(cfg["router_small_max"], 12)
        self.assertEqual(cfg["router_medium_max"], 40)
        self.assertNotIn("junk", cfg)

    def test_thresholds_kept_ordered(self):
        cfg = load_config({"router_small_max": 60, "router_medium_max": 30})
        self.assertGreater(cfg["router_medium_max"], cfg["router_small_max"])

    def test_router_from_config(self):
        r = router_from_config({"router_small_max": 10})
        self.assertEqual(r.small_max, 10)
        self.assertEqual(r.medium_max, MEDIUM_MAX)

    def test_orchestrator_adopts_config(self):
        orch = Orchestrator.default(config={"router_small_max": 9, "router_medium_max": 19})
        self.assertEqual(orch.router.small_max, 9)
        self.assertEqual(orch.router.medium_max, 19)
        # default (no config) keeps the baseline
        self.assertEqual(Orchestrator.default().router.small_max, SMALL_MAX)

    def test_calibrate_apply_roundtrip(self):
        recs = [rec("small", "revise")] * 8 + [rec("small", "accept")] * 2  # weak small tier
        cal = EvaluationStore().calibrate(recs)
        adopt = {"router_small_max": cal.proposed["router_small_max"],
                 "router_medium_max": cal.proposed["router_medium_max"]}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cfg.json"
            p.write_text(json.dumps(adopt), encoding="utf-8")
            loaded = load_config(str(p))
            self.assertEqual(loaded["router_small_max"], adopt["router_small_max"])
            self.assertLess(loaded["router_small_max"], SMALL_MAX)  # proposal actually tightened it


if __name__ == "__main__":
    unittest.main()
