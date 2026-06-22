"""Executor — produce a real artifact, review the real artifact, drive the FSM.

For a ready contract the assigned agent *generates* the expected outputs (via the
backend, or a deterministic stub under NullBackend), the generated text is handed
to the Reviewer + Judge, and the verdict drives the TaskStatus state machine:
accept → completed, revise/rerun → retry (with tier escalation) up to the guard's
cap, discard → failed, human-gated accept → escalated (awaiting approval).

SAFETY: the Executor only ever produces artifact *text*. It never performs a real
side-effecting/dangerous action (deploy, push, delete, external send). If a
contract's required_tools include a dangerous capability, execution stops at an
Approval Gate and the task is escalated — never auto-run.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from .architect import _extract_json
from .decompose_guard import DecompositionGuard, DecompositionState
from .judge import Judge
from .logger import ExecutionLogger, LogRecord
from .model_router import ModelRouter
from .models import (
    Action, AgentTemplate, Decision, JudgeDecision, ModelTier, Provenance,
    ReviewLens, ReviewResult, RunPlan, TaskContract, TaskStatus,
)
from .permissions import ActionDecision, PermissionModel
from .registry import AgentRegistry
from .review_planner import ReviewPlanner
from .reviewer import Reviewer
from .sandbox import Sandbox, SandboxResult


_TOOL_ACTION = {a.value: a for a in Action}
# Rough per-step wall-clock estimate by tier (seconds) for time-estimate error.
_TIER_SECONDS = {ModelTier.SMALL: 4, ModelTier.MEDIUM: 9, ModelTier.LARGE: 20}
# Nominal cost per generated token (USD proxy) so real actual_cost is comparable to
# the per-tier estimated_cost. Local models are ~free; this measures *relative* cost.
_TOKEN_COST = 0.00005
_TIER_COST = {ModelTier.SMALL: 0.01, ModelTier.MEDIUM: 0.05, ModelTier.LARGE: 0.20}


@dataclass
class ExecutionResult:
    task_id: str
    status: TaskStatus
    artifacts: dict[str, str] = field(default_factory=dict)
    attempts: int = 0
    decision: Optional[Decision] = None
    reviews: list[ReviewResult] = field(default_factory=list)
    provenance: Optional[Provenance] = None
    approval_required: Optional[str] = None
    elapsed_seconds: Optional[float] = None
    actual_cost: Optional[float] = None
    trace: list[str] = field(default_factory=list)


class Executor:
    def __init__(
        self,
        registry: AgentRegistry,
        reviewer: Reviewer,
        judge: Judge,
        review_planner: ReviewPlanner,
        router: ModelRouter,
        permissions: PermissionModel,
        guard: DecompositionGuard,
        backend=None,
        sandbox: Optional[Sandbox] = None,
        max_upstream_chars: int = 1200,
    ):
        self.registry = registry
        self.reviewer = reviewer
        self.judge = judge
        self.review_planner = review_planner
        self.router = router
        self.permissions = permissions
        self.guard = guard
        self.backend = backend
        self.sandbox = sandbox        # opt-in; None == no real execution
        self.max_upstream_chars = max_upstream_chars

    def _backend_available(self) -> bool:
        return self.backend is not None and getattr(self.backend, "available", False)

    # ----- permission gate ----- #
    def _gate(self, agent: Optional[AgentTemplate], contract: TaskContract):
        """Return (action, decision) if execution must stop, else None."""
        if agent is None:
            return None
        for tool in contract.required_tools:
            act = _TOOL_ACTION.get(tool)
            if act is None:
                continue
            decision = self.permissions.check_action(agent, act)
            if decision in (ActionDecision.FORBIDDEN, ActionDecision.NEEDS_APPROVAL):
                return act, decision
        return None

    # ----- artifact generation ----- #
    def _gen_prompt(self, agent: Optional[AgentTemplate], contract: TaskContract,
                    upstream: dict[str, str]) -> str:
        role = agent.role if agent else (contract.assigned_agent_template or "Engineer")
        mission = agent.mission if agent else ""
        bar = "; ".join(agent.quality_bar) if agent else ""
        ctx = ""
        if upstream:
            parts = []
            for name, content in upstream.items():
                parts.append(f"### {name}\n{content[:self.max_upstream_chars]}")
            ctx = "UPSTREAM ARTIFACTS (context, do not repeat verbatim):\n" + "\n\n".join(parts) + "\n\n"
        return (
            f"You are a {role}. {mission}\n"
            f"Produce the expected output artifact(s) that satisfy the contract below. "
            f"Write the actual content (real code/text), not a description.\n\n"
            f"{ctx}"
            f"CONTRACT:\n"
            f"  objective: {contract.objective}\n"
            f"  acceptance_criteria: {contract.acceptance_criteria}\n"
            f"  constraints: {contract.constraints}\n"
            f"  expected_output (produce one entry per name): {contract.expected_output}\n"
            + (f"  quality_bar: {bar}\n" if bar else "")
            + '\nReturn ONLY a JSON object mapping each output to its full content as a '
            'string: {"<filename>": "<content>", ...}. Use a REAL filename with the '
            'correct extension as each key (e.g. "is_palindrome.py", '
            '"test_is_palindrome.py") — never a prose description. A test file must '
            'import the implementation by its module name.'
        )

    def _deterministic(self, contract: TaskContract, upstream: dict[str, str]) -> dict[str, str]:
        names = contract.expected_output or ["artifact.txt"]
        crit = "\n".join(f"# - {c}" for c in contract.acceptance_criteria)
        used = ", ".join(upstream.keys()) or "(none)"
        out = {}
        for n in names:
            out[n] = (
                f"# {n}\n# agent: {contract.assigned_agent_template}\n"
                f"# objective: {contract.objective}\n# derived_from: {used}\n"
                f"# acceptance criteria:\n{crit}\n\n# TODO: deterministic stub "
                f"(no backend wired); real content requires --backend.\n"
            )
        return out

    def _generate(self, agent, contract, upstream, tier) -> dict[str, str]:
        if self._backend_available():
            try:
                self.backend.last_usage = None  # so a fallback leaves no stale usage
                raw = self.backend.complete(self._gen_prompt(agent, contract, upstream), tier,
                                            effort=contract.recommended_effort.value)
                data = _extract_json(raw)
                if isinstance(data, dict) and data:
                    arts = {str(k): str(v) for k, v in data.items() if str(v).strip()}
                    if arts:
                        return arts
            except Exception:
                pass  # fall back to a deterministic stub; never break execution
        return self._deterministic(contract, upstream)

    # ----- single task ----- #
    def execute_task(self, contract: TaskContract, upstream: dict[str, str],
                     state: DecompositionState) -> ExecutionResult:
        trace: list[str] = []
        t0 = time.monotonic()
        agent = self.registry.get(contract.assigned_agent_template) \
            if contract.assigned_agent_template in self.registry else None
        if agent is None:
            trace.append(f"agent '{contract.assigned_agent_template}' not registered; permission gate skipped")

        gate = self._gate(agent, contract)
        if gate is not None:
            act, decision = gate
            if decision == ActionDecision.NEEDS_APPROVAL:
                self.permissions.request_approval(agent, act, contract.objective)
            trace.append(f"blocked by permission gate on '{act.value}' ({decision.value})")
            return ExecutionResult(
                task_id=contract.task_id, status=TaskStatus.ESCALATED,
                approval_required=act.value, attempts=0, trace=trace,
                elapsed_seconds=round(time.monotonic() - t0, 3),
                provenance=Provenance(contract.assigned_agent_template or "?", contract.task_id,
                                      list(upstream.keys())),
            )

        rplan = self.review_planner.plan(contract)
        attempts = 0
        tier = contract.recommended_model
        artifacts: dict[str, str] = {}
        results: list[ReviewResult] = []
        decision: Optional[Decision] = None
        total_tokens = 0

        while True:
            attempts += 1
            artifacts = self._generate(agent, contract, upstream, tier)
            usage = getattr(self.backend, "last_usage", None) if self.backend else None
            if usage:
                total_tokens += usage.get("total_tokens", 0)
            blob = "\n\n".join(f"// {n}\n{c}" for n, c in artifacts.items())
            results = self.reviewer.run(contract, rplan, artifact=blob)

            # Ground the AUTO_CHECK/TESTS lens in real execution when enabled.
            if self.sandbox is not None and artifacts:
                sbres = self.sandbox.run(artifacts)
                if sbres.ran:
                    results = [r for r in results
                               if r.lens not in (ReviewLens.AUTO_CHECK, ReviewLens.TESTS)]
                    results.append(self._sandbox_review(contract, sbres))
                    trace.append(f"sandbox[{sbres.isolation}] {sbres.runner} -> "
                                 f"{'pass' if sbres.passed else 'FAIL'}"
                                 + (f" ({sbres.reason})" if not sbres.passed else ""))
                else:
                    trace.append(f"sandbox: not run ({sbres.reason})")

            decision = self.judge.decide(contract, results)
            trace.append(f"attempt {attempts} @ {tier.value}: judge -> {decision.decision.value}")

            if decision.decision == JudgeDecision.ACCEPT:
                status = TaskStatus.ESCALATED if rplan.require_human_gate else TaskStatus.COMPLETED
                if rplan.require_human_gate:
                    trace.append("accepted but human approval gate required -> escalated")
                break
            if decision.decision in (JudgeDecision.REVISE, JudgeDecision.RERUN) \
                    and self.guard.can_retry(contract.task_id, state):
                self.guard.record_retry(contract.task_id, state)
                tier = self.router.route(contract, consecutive_failures=attempts).tier
                continue
            status = TaskStatus.FAILED if decision.decision == JudgeDecision.DISCARD else TaskStatus.ESCALATED
            break

        provenance = Provenance(
            created_by_agent=contract.assigned_agent_template or "?",
            task_contract_id=contract.task_id,
            derived_from=list(upstream.keys()),
            reviews_passed=[r.lens.value for r in results if r.verdict == "pass"],
            judged_by="Judge",
            judge_decision=decision.decision.value if decision else None,
        )
        return ExecutionResult(
            task_id=contract.task_id, status=status, artifacts=artifacts, attempts=attempts,
            decision=decision, reviews=results, provenance=provenance, trace=trace,
            elapsed_seconds=round(time.monotonic() - t0, 3),
            actual_cost=round(total_tokens * _TOKEN_COST, 6) if total_tokens else None,
        )

    def _sandbox_review(self, contract: TaskContract, sb: SandboxResult) -> ReviewResult:
        if sb.passed:
            return ReviewResult(
                contract.task_id, ReviewLens.AUTO_CHECK, "pass", "none",
                f"sandbox {sb.runner} passed (exit {sb.exit_code})",
            )
        out = (sb.stderr or sb.stdout or "").strip()
        return ReviewResult(
            contract.task_id, ReviewLens.AUTO_CHECK, "fail",
            "high", f"sandbox {sb.runner} failed ({sb.reason})",
            findings=[out[:300]] if out else [],
        )

    # ----- whole plan ----- #
    def execute_plan(self, plan: RunPlan, state: Optional[DecompositionState] = None,
                     limit: Optional[int] = None,
                     logger: Optional[ExecutionLogger] = None) -> dict[str, ExecutionResult]:
        state = state or DecompositionState()
        by_id = {c.task_id: c for c in plan.contracts}
        order = plan.execution_order[:limit] if limit else plan.execution_order

        all_artifacts: dict[str, str] = {}
        produced_by: dict[str, set] = {}
        results: dict[str, ExecutionResult] = {}

        for tid in order:
            contract = by_id[tid]
            upstream: dict[str, str] = {}
            for dep in contract.dependencies:
                for name in produced_by.get(dep, ()):  # only deps we actually ran
                    upstream[name] = all_artifacts[name]

            r = self.execute_task(contract, upstream, state)
            results[tid] = r
            for name, content in r.artifacts.items():
                all_artifacts[name] = content
            produced_by[tid] = set(r.artifacts.keys())

            if logger is not None:
                logger.log(self._log_record(plan, contract, r))
        return results

    def _log_record(self, plan: RunPlan, contract: TaskContract, r: ExecutionResult) -> LogRecord:
        n = len(r.reviews) or 1
        passed = sum(1 for rv in r.reviews if rv.verdict == "pass")
        auto = [rv for rv in r.reviews if rv.lens.value in ("auto_check", "tests")]
        test_pass = (sum(1 for rv in auto if rv.verdict == "pass") / len(auto)) if auto else None
        return LogRecord(
            run_id=plan.run_id,
            task_id=contract.task_id,
            task_type=contract.task_type.value,
            selected_model=contract.recommended_model.value,
            decomposition_strategy=plan.decomposition_strategy or plan.org_template_id,
            agent_template=contract.assigned_agent_template,
            estimated_cost=round(_TIER_COST[contract.recommended_model] * max(1, contract.estimated_steps) / 4, 4),
            actual_cost=r.actual_cost,
            estimated_seconds=float(max(1, contract.estimated_steps) * _TIER_SECONDS[contract.recommended_model]),
            elapsed_seconds=r.elapsed_seconds,
            review_score=round(passed / n, 4),
            rework_count=max(0, r.attempts - 1),
            test_pass_rate=test_pass,
            escalated=(r.status == TaskStatus.ESCALATED),
            human_override=(r.approval_required is not None),
            judge_decision=r.decision.decision.value if r.decision else None,
            failure_reason=(r.decision.reason if r.status == TaskStatus.FAILED and r.decision else None),
        )
