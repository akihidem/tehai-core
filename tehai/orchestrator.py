"""Orchestrator — the conductor.

Pipeline (all deterministic in the MVP):
    request
      -> classify into an Organization Template
      -> TaskArchitect builds a DAG of contract-bound subtasks (guarded recursion)
      -> Agent Designer confirms each agent from the Registry (never fabricates)
      -> Model Router sets the cheapest capable tier (with hazard escalation)
      -> Permission model enforces child ⊆ parent capabilities
      -> Review Planner attaches a risk-based review plan per task
      -> topological execution order
      -> RunPlan (+ optional sample execution log)
"""

from __future__ import annotations

from collections import deque
from typing import Optional

from .architect import TaskArchitect
from .backends import ModelBackend, get_backend
from .config import router_from_config
from .decompose_guard import DecompositionGuard
from .evaluation import EvaluationStore
from .executor import ExecutionResult, Executor
from .judge import Judge
from .logger import ExecutionLogger, LogRecord
from .model_router import ModelRouter
from .models import (
    Decision, Effort, ModelTier, ReviewPlan, ReviewResult, RunPlan, TaskContract,
    TaskStatus, TaskType, max_effort,
)
from .org_templates import select_org_template
from .permissions import PermissionModel
from .registry import AgentRegistry
from .review_planner import ReviewPlanner
from .reviewer import Reviewer
from .sandbox import Sandbox
from .scoring import Scorer


# Rough per-task cost estimate by tier (USD). For logging/calibration only.
_TIER_COST = {ModelTier.SMALL: 0.01, ModelTier.MEDIUM: 0.05, ModelTier.LARGE: 0.20}
# Rough per-step wall-clock estimate by tier (seconds). Calibration seed only.
_TIER_SECONDS = {ModelTier.SMALL: 4, ModelTier.MEDIUM: 9, ModelTier.LARGE: 20}


class OrchestratorError(Exception):
    pass


def topological_order(contracts: list[TaskContract]) -> list[str]:
    """Kahn's algorithm over the contract DAG. Raises on cycle."""
    ids = {c.task_id for c in contracts}
    indeg = {c.task_id: 0 for c in contracts}
    adj: dict[str, list[str]] = {c.task_id: [] for c in contracts}
    for c in contracts:
        for dep in c.dependencies:
            if dep in ids:
                adj[dep].append(c.task_id)
                indeg[c.task_id] += 1
    # Deterministic tie-break by task_id.
    q = deque(sorted(t for t, d in indeg.items() if d == 0))
    order: list[str] = []
    while q:
        n = q.popleft()
        order.append(n)
        for m in sorted(adj[n]):
            indeg[m] -= 1
            if indeg[m] == 0:
                q.append(m)
    if len(order) != len(contracts):
        raise OrchestratorError("cycle detected in task DAG")
    return order


class Orchestrator:
    def __init__(
        self,
        registry: AgentRegistry,
        router: Optional[ModelRouter] = None,
        architect: Optional[TaskArchitect] = None,
        review_planner: Optional[ReviewPlanner] = None,
        judge: Optional[Judge] = None,
        permissions: Optional[PermissionModel] = None,
        guard: Optional[DecompositionGuard] = None,
        backend: Optional[ModelBackend] = None,
        sandbox: Optional[Sandbox] = None,
    ):
        self.registry = registry
        self.backend = backend
        self.sandbox = sandbox
        self.guard = guard or DecompositionGuard()
        self.router = router or ModelRouter()
        self.architect = architect or TaskArchitect(guard=self.guard, backend=backend)
        self.review_planner = review_planner or ReviewPlanner()
        self.judge = judge or Judge()
        self.permissions = permissions or PermissionModel()
        self.evaluation = EvaluationStore()
        self.scorer = Scorer(backend)
        self.reviewer = Reviewer(backend)
        self.executor = Executor(
            self.registry, self.reviewer, self.judge, self.review_planner,
            self.router, self.permissions, self.guard, backend, sandbox=sandbox,
        )

    @classmethod
    def default(cls, backend: str | ModelBackend = "null",
                sandbox: bool | Sandbox = False, config=None,
                **backend_kwargs) -> "Orchestrator":
        """Build with the default registry. ``backend`` may be a name
        ("null"/"echo"/"claude-cli"/"ollama") or a ready ModelBackend. ``sandbox``
        enables real execution of generated artifacts (opt-in; True or a Sandbox).
        ``config`` (path or dict) adopts human-reviewed calibration knobs (router
        thresholds)."""
        be = backend if isinstance(backend, ModelBackend) else get_backend(backend, **backend_kwargs)
        sb = sandbox if isinstance(sandbox, Sandbox) else (Sandbox() if sandbox else None)
        router = router_from_config(config) if config is not None else None
        return cls(registry=AgentRegistry.load(), backend=be, sandbox=sb, router=router)

    # ----- Agent Designer ----- #
    def _assign_agent(self, contract: TaskContract) -> None:
        try:
            chosen = self.registry.select_for_task(
                contract.task_type, preferred_id=contract.assigned_agent_template
            )
            contract.assigned_agent_template = chosen.agent_template_id
        except Exception:
            # Record a proposal; do NOT fabricate an agent. Leave the org's
            # suggested role in place so the plan is still inspectable.
            self.registry.propose_new_template(
                role=contract.assigned_agent_template or "unknown",
                reason=f"no registered template handles {contract.task_type.value}",
                task_type=contract.task_type,
            )

    # ----- planning ----- #
    def plan(self, request: str, run_id: Optional[str] = None, org=None) -> RunPlan:
        if not request or not request.strip():
            raise OrchestratorError("empty request")
        run_id = run_id or self._derive_run_id(request)

        # `org` lets a caller (e.g. a Team Orchestrator) inject a specific
        # organization template instead of keyword-classifying the request.
        org = org or select_org_template(request)
        contracts, state = self.architect.decompose(request, org, run_id)
        strategy = self.architect.last_strategy

        if strategy == "llm":
            decomposition_strategy = f"{org.org_template_id}:llm"
            assumptions = [
                f"分解は LLM バックエンド '{getattr(self.architect.backend, 'name', '?')}' が提案・"
                f"決定的ガード（契約検証/循環/委譲上限）で境界付け。スコアは分解応答に同梱。",
                f"組織テンプレート '{org.org_template_id}' のロールを LLM に提示。",
            ]
        else:
            # Heuristic structure; still route scores through the backend if present.
            rescored = self.scorer.rescore(contracts, request)
            decomposition_strategy = f"{org.org_template_id}:template" + ("+llmscore" if rescored else "")
            assumptions = [
                "分解は決定的ヒューリスティック（LLMバックエンド未使用または失敗→フォールバック）。",
                f"組織テンプレートはキーワード分類で '{org.org_template_id}' を選択。",
                ("スコアは LLM バックエンドで一括再採点。" if rescored
                 else "スコアはヒューリスティック（ASSUMPTIONS.md 参照）。"),
            ]
            if self.architect.last_error:
                assumptions.append(f"LLM分解は失敗しフォールバック: {self.architect.last_error}")
            if not rescored and self.scorer.last_error:
                assumptions.append(f"LLM再採点も失敗: {self.scorer.last_error}")

        by_id = {c.task_id: c for c in contracts}

        for c in contracts:
            # Agent Designer: confirm from registry.
            self._assign_agent(c)
            # Model Router: cheapest capable tier + hazard escalation, AND a
            # per-task reasoning effort floored by the assigned agent's baseline.
            routing = self.router.route(c)
            c.recommended_model = routing.tier
            agent_eff = (self.registry.get(c.assigned_agent_template).recommended_effort
                         if c.assigned_agent_template in self.registry else Effort.MEDIUM)
            c.recommended_effort = max_effort(routing.effort, agent_eff)
            # Contract integrity gate: invalid contract must not be "ready".
            if c.validate():
                c.status = TaskStatus.BLOCKED
                assumptions.append(f"{c.task_id}: 契約不備のため BLOCKED ({c.validate()[0]})")
            else:
                c.status = TaskStatus.READY

        # Permission enforcement: subdivided child ⊆ parent capabilities.
        for c in contracts:
            if c.parent_task_id and c.parent_task_id in by_id:
                parent = by_id[c.parent_task_id]
                if parent.assigned_agent_template in self.registry and \
                        c.assigned_agent_template in self.registry:
                    chk = self.permissions.enforce_child_subset(
                        self.registry.get(parent.assigned_agent_template),
                        self.registry.get(c.assigned_agent_template),
                    )
                    if not chk.ok:
                        c.status = TaskStatus.BLOCKED
                        assumptions.append(f"{c.task_id}: 権限違反のため BLOCKED ({chk.detail})")

        order = topological_order(contracts)
        review_plans = {c.task_id: self.review_planner.plan(c) for c in contracts}

        # Top-level task type = the org's first phase type (best single label).
        top_type = contracts[0].task_type if contracts else TaskType.GENERIC

        return RunPlan(
            run_id=run_id,
            request=request,
            task_type=top_type,
            org_template_id=org.org_template_id,
            contracts=contracts,
            execution_order=order,
            review_plans=review_plans,
            assumptions=assumptions,
            decomposition_strategy=decomposition_strategy,
        )

    @staticmethod
    def _derive_run_id(request: str) -> str:
        # Deterministic, content-derived id (no wall-clock) so plans are reproducible.
        import hashlib
        h = hashlib.sha1(request.strip().encode("utf-8")).hexdigest()[:8]
        return f"R-{h}"

    # ----- full execution (generate -> review -> judge -> FSM) ----- #
    def execute(self, plan: RunPlan, limit: Optional[int] = None,
                logger=None, state=None) -> dict[str, ExecutionResult]:
        """Generate real artifacts for the plan's tasks (topological order,
        upstream artifacts threaded in), review each artifact, and let the Judge
        drive the lifecycle. Bounded by ``limit`` to cap backend calls."""
        return self.executor.execute_plan(plan, state=state, limit=limit, logger=logger)

    # ----- review execution (plan -> review -> judge) ----- #
    def review_and_judge(
        self, contract: TaskContract, artifact: Optional[str] = None,
    ) -> tuple[ReviewPlan, list[ReviewResult], Decision]:
        """Run the risk-based review plan for a contract through the backend (or
        the deterministic fallback reviewer) and let the Judge decide. When
        ``artifact`` is None the lenses review the contract/plan itself."""
        rplan = self.review_planner.plan(contract)
        results = self.reviewer.run(contract, rplan, artifact)
        decision = self.judge.decide(contract, results)
        return rplan, results, decision

    # ----- sample execution log (planning-phase seed) ----- #
    def emit_sample_log(self, plan: RunPlan, logger: ExecutionLogger) -> list[LogRecord]:
        """Write one estimated log row per task. These are *plan-time* estimates,
        not results of a real run — they seed the evaluation store."""
        records: list[LogRecord] = []
        for tid in plan.execution_order:
            c = next(c for c in plan.contracts if c.task_id == tid)
            rp = plan.review_plans[tid]
            est_cost = round(_TIER_COST[c.recommended_model] * max(1, c.estimated_steps) / 4, 4)
            # Crude plan-time duration estimate: steps × per-tier seconds.
            est_seconds = float(max(1, c.estimated_steps) * _TIER_SECONDS[c.recommended_model])
            rec = LogRecord(
                run_id=plan.run_id,
                task_id=c.task_id,
                task_type=c.task_type.value,
                selected_model=c.recommended_model.value,
                decomposition_strategy=plan.decomposition_strategy or plan.org_template_id,
                agent_template=c.assigned_agent_template,
                estimated_cost=est_cost,
                actual_cost=None,
                estimated_seconds=est_seconds,
                elapsed_seconds=None,
                review_score=None,
                rework_count=0,
                test_pass_rate=None,
                escalated=(c.status == TaskStatus.ESCALATED),
                human_override=rp.require_human_gate,
                judge_decision=None,
                failure_reason=None if c.status != TaskStatus.BLOCKED else "blocked_at_plan_time",
            )
            records.append(logger.log(rec))
        return records
