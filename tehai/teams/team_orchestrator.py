"""Team Orchestrator.

Receives a Team Contract, decomposes it into tehai Task Contracts (reusing the
existing Task Architect via a team-derived OrgTemplate), runs the per-task
pipeline (agent assignment, model routing, risk-based review, judge, execution),
enforces the team's task-type boundary, and returns a TeamResult.
"""

from __future__ import annotations

from typing import Optional

from ..models import TaskStatus, TaskType
from .failure_router import FailureRouter, FailureSignal
from .models import Failure, FailureType, TeamContract, TeamResult, TeamTaskStatus


# tehai task_type -> failure_type when a task of that type fails
_FAILURE_BY_TASKTYPE = {
    TaskType.TEST_AUTHORING: FailureType.TEST_FAILURE,
    TaskType.QA: FailureType.TEST_FAILURE,
    TaskType.CODE_IMPLEMENTATION: FailureType.IMPLEMENTATION_ERROR,
    TaskType.CODE_REVIEW: FailureType.IMPLEMENTATION_ERROR,
    TaskType.SECURITY_REVIEW: FailureType.SECURITY_RISK,
    TaskType.INTEGRATION: FailureType.INTEGRATION_CONFLICT,
    TaskType.ARCHITECTURE: FailureType.ARCHITECTURE_CONFLICT,
    TaskType.SPEC_DESIGN: FailureType.REQUIREMENT_AMBIGUITY,
}


class TeamOrchestrator:
    def __init__(self, orchestrator, registry, failure_router: Optional[FailureRouter] = None):
        self.orchestrator = orchestrator     # tehai Orchestrator
        self.registry = registry             # TeamRegistry
        self.router = failure_router or FailureRouter()

    def run(self, contract: TeamContract,
            forced_failure: Optional[FailureType] = None,
            counter: int = 1) -> TeamResult:
        team = self.registry.get(contract.assigned_team)

        # 1) Contract integrity — an invalid team contract is a requirement gap.
        errs = contract.validate()
        if errs:
            f = self.router.classify(FailureSignal(
                task_id=contract.team_task_id, detected_by="team_orchestrator",
                failure_type=FailureType.REQUIREMENT_AMBIGUITY, evidence=errs[:2]), counter)
            return TeamResult(contract.team_task_id, team.team_id, TeamTaskStatus.RETURNED_FOR_REVISION,
                              failures=[f], loop_count=contract.loop_count)

        # 2) Decompose via the team's internal pipeline (reuses the Task Architect).
        org = self.registry.to_org_template(team)
        plan = self.orchestrator.plan(contract.objective, run_id=contract.team_task_id, org=org)

        # 3) Boundary: a decomposed task must not be a forbidden task type.
        for c in plan.contracts:
            if self.registry.forbids(team, c.task_type.value):
                f = self.router.classify(FailureSignal(
                    task_id=c.task_id, detected_by=team.team_id,
                    failure_type=FailureType.PERMISSION_VIOLATION,
                    evidence=[f"task_type '{c.task_type.value}' is forbidden for {team.team_id}"]), counter)
                return TeamResult(contract.team_task_id, team.team_id, TeamTaskStatus.ESCALATED,
                                  plan=plan, failures=[f], loop_count=contract.loop_count)

        # 4) Injected/forced failure (used to exercise failure routing deterministically).
        if forced_failure is not None:
            f = self.router.classify(FailureSignal(
                task_id=contract.team_task_id, detected_by=team.team_id,
                failure_type=forced_failure,
                security_finding=(forced_failure == FailureType.SECURITY_RISK),
                evidence=[f"injected {forced_failure.value} for verification"]), counter)
            return TeamResult(contract.team_task_id, team.team_id, TeamTaskStatus.FAILED,
                              plan=plan, failures=[f], loop_count=contract.loop_count)

        # 5) Execute the task DAG (deterministic by default; LLM/sandbox if wired).
        results = self.orchestrator.execute(plan)
        artifacts: dict = {}
        failures: list[Failure] = []
        for tid, r in results.items():
            artifacts.update(r.artifacts)
            if r.status == TaskStatus.FAILED:
                c = next(c for c in plan.contracts if c.task_id == tid)
                ft = _FAILURE_BY_TASKTYPE.get(c.task_type, FailureType.IMPLEMENTATION_ERROR)
                failures.append(self.router.classify(FailureSignal(
                    task_id=tid, detected_by=team.team_id, failure_type=ft,
                    evidence=[r.decision.reason if r.decision else "task failed"]), counter))

        status = TeamTaskStatus.COMPLETED if not failures else TeamTaskStatus.FAILED
        return TeamResult(contract.team_task_id, team.team_id, status, plan=plan,
                          task_results=results, artifacts=artifacts, failures=failures,
                          loop_count=contract.loop_count)
