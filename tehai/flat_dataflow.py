"""FlatDataflowOrchestrator (B) — the verification-centric flat alternative.

This is the structural counter-proposal to the human-org hierarchy
(`tehai.teams.MetaOrchestrator`, "A"). Where A distributes a goal across 7 named
teams in a Meta→Team→Agent hierarchy and, on failure, re-runs the root-cause team
*and its whole downstream subtree* (cascading reroute), B does the opposite:

  * **Flat** — one task DAG (the goal's org template), no team layer. tehai's
    single-team `Orchestrator` is *already* a flat generate→review→judge pipeline,
    so B is a thin wrapper over it, not a rebuild.
  * **Verification-centric** — every node passes through external-anchored
    verification (the separated review lenses + Judge). Verification is the spine,
    not a per-team afterthought.
  * **Node-local recovery** — a defect caught at a node is re-run *at that node
    only* (bounded). Nothing downstream is re-run, because nothing downstream has
    run yet (topological order). Exceed the local bound → a human gate.

The key consequence vs A: because verification is external at *every* node, a
"blind-spot" defect (one the producer's own review would share a blind spot on)
is caught at the node instead of slipping downstream — so B does not let such
defects escape. See the A/B experiment in ``experiments/`` for the measured
comparison.

Injected defects (deterministic, for the experiment) are the *same* mechanism the
multi-team `TeamOrchestrator.forced_failure` uses, but at node granularity with a
local retry loop instead of a subtree reroute.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .costmodel import exec_cost
from .models import TaskStatus, TaskType
from .orchestrator import Orchestrator
from .review_planner import ReviewPlanner


# A task's logical "stage", so a defect can be addressed to the same logical
# point in both architectures (A keys defects by team, B by stage→task_type).
STAGE_OF_TASKTYPE: dict[TaskType, str] = {
    TaskType.SPEC_DESIGN: "requirements",
    TaskType.RESEARCH: "requirements",
    TaskType.ARCHITECTURE: "architecture",
    TaskType.CODE_IMPLEMENTATION: "implementation",
    TaskType.CONTENT: "implementation",
    TaskType.GENERIC: "implementation",
    TaskType.TEST_AUTHORING: "verification",
    TaskType.QA: "verification",
    TaskType.CODE_REVIEW: "verification",
    TaskType.SECURITY_REVIEW: "security",
    TaskType.INTEGRATION: "integration",
    TaskType.RELEASE: "integration",
    TaskType.DOC_FORMATTING: "documentation",
    TaskType.SUMMARIZATION: "documentation",
    TaskType.EXTRACTION: "documentation",
    TaskType.CLASSIFICATION: "documentation",
}


@dataclass
class Defect:
    """A deterministic, injected defect for the experiment.

    ``resolve_after`` = number of node-local re-runs needed before it is fixed.
    ``blind_spot`` only matters for A (the producer's own review misses it); B
    catches every defect at the node regardless, so the flag is carried purely
    for reporting symmetry.
    """

    failure_type: str
    resolve_after: int = 1
    blind_spot: bool = False


@dataclass
class FlatRunResult:
    run_id: str
    goal: str
    node_order: list[str] = field(default_factory=list)
    node_records: list[dict] = field(default_factory=list)
    final_status: str = "completed"
    human_intervention_required: bool = False
    escaped_defects: int = 0
    model_calls: int = 0
    cost_usd: float = 0.0
    local_retries: int = 0
    assumptions: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "node_order": list(self.node_order),
            "node_records": list(self.node_records),
            "final_status": self.final_status,
            "human_intervention_required": self.human_intervention_required,
            "escaped_defects": self.escaped_defects,
            "model_calls": self.model_calls,
            "cost_usd": self.cost_usd,
            "local_retries": self.local_retries,
            "assumptions": list(self.assumptions),
            "metrics": dict(self.metrics),
        }


class FlatDataflowOrchestrator:
    def __init__(self, orchestrator: Orchestrator,
                 review_planner: Optional[ReviewPlanner] = None,
                 local_retry_cap: int = 2):
        self.orchestrator = orchestrator
        self.review_planner = review_planner or ReviewPlanner()
        self.local_retry_cap = local_retry_cap

    @classmethod
    def default(cls, backend: str = "null", sandbox: bool = False,
                local_retry_cap: int = 2) -> "FlatDataflowOrchestrator":
        return cls(Orchestrator.default(backend, sandbox=sandbox),
                   local_retry_cap=local_retry_cap)

    def run(self, goal: str, injected_defects: Optional[dict] = None,
            run_id: Optional[str] = None) -> FlatRunResult:
        if not goal or not goal.strip():
            raise ValueError("empty goal")
        defects: dict[str, Defect] = injected_defects or {}

        # 1) Plan the flat DAG and run it for real: generate→review→judge per
        #    node, linear, no reroute. (This is the actual tehai single-team pipe.)
        plan = self.orchestrator.plan(goal, run_id=run_id)
        results = self.orchestrator.execute(plan)
        by_id = {c.task_id: c for c in plan.contracts}

        calls = 0
        usd = 0.0
        local_retries = 0
        human = False
        node_records: list[dict] = []

        for tid in plan.execution_order:
            c = by_id[tid]
            r = results[tid]
            rplan = self.review_planner.plan(c)
            stage = STAGE_OF_TASKTYPE.get(c.task_type, "implementation")

            # Real baseline cost (r.attempts already folds in any real revise loop).
            base_calls, base_usd = exec_cost(c, rplan, max(1, r.attempts))
            calls += base_calls
            usd += base_usd

            defect = defects.get(stage)
            node_human = False
            caught = False
            resolved = True
            extra_attempts = 0

            if defect is not None:
                # Every node is externally verified, so the defect is caught HERE
                # (overt and blind-spot alike) and re-run locally until fixed.
                caught = True
                extra_attempts = min(defect.resolve_after, self.local_retry_cap + 1)
                e_calls, e_usd = exec_cost(c, rplan, extra_attempts)
                calls += e_calls
                usd += e_usd
                local_retries += min(defect.resolve_after, self.local_retry_cap)
                if defect.resolve_after > self.local_retry_cap:
                    resolved = False
                    node_human = True          # exceeded the local bound -> human gate

            if node_human:
                human = True

            node_records.append({
                "task_id": tid,
                "task_type": c.task_type.value,
                "stage": stage,
                "real_status": r.status.value,
                "real_attempts": r.attempts,
                "injected": defect.failure_type if defect else None,
                "blind_spot": (defect.blind_spot if defect else None),
                "caught_at_node": caught,
                "resolved": resolved,
                "local_reruns": min(defect.resolve_after, self.local_retry_cap) if defect else 0,
                "human": node_human,
            })

        final_status = "completed" if not human else "needs_human"
        usd = round(usd, 4)
        metrics = {
            "system_type": "flat_verification_dataflow",
            "task_success": final_status == "completed",
            # B verifies every node externally, so a caught defect never escapes.
            "true_success": final_status == "completed",
            "autonomous_completion": final_status == "completed",
            "human_intervention_required": human,
            "human_intervention_rate": 1.0 if human else 0.0,
            "escaped_defects": 0,
            "node_count": len(plan.execution_order),
            "model_calls": calls,
            "cost_usd": usd,
            "local_retries": local_retries,
        }
        return FlatRunResult(
            run_id=plan.run_id, goal=goal, node_order=list(plan.execution_order),
            node_records=node_records, final_status=final_status,
            human_intervention_required=human, escaped_defects=0,
            model_calls=calls, cost_usd=usd, local_retries=local_retries,
            assumptions=[
                "平らなデータフロー: 単一の task DAG（チーム階層なし）。",
                "全ノードを外部錨検証（分離レビュー+Judge）→ 失敗はノード内ローカル再試行。",
                f"ローカル再試行上限 {self.local_retry_cap} を超えたら人間ゲート（盲目自律しない）。",
                "外部検証が全ノードに掛かるため、blind-spot 欠陥もノードで捕捉＝流出ゼロ。",
            ],
            metrics=metrics,
        )
