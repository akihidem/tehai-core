"""Data models for the Multi-Team layer that sits on top of the tehai primitives.

A Team Contract is to a Team what a Task Contract is to an agent: the unit of
controlled delegation. The Meta Orchestrator assigns *teams* (not agents);
each Team Orchestrator decomposes its Team Contract into tehai Task Contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# Lifecycles
# --------------------------------------------------------------------------- #
class TeamTaskStatus(str, Enum):
    PLANNED = "planned"
    ASSIGNED = "assigned"
    DECOMPOSING = "decomposing"
    EXECUTING = "executing"
    WAITING_FOR_DEPENDENCY = "waiting_for_dependency"
    REVIEWING = "reviewing"
    RETURNED_FOR_REVISION = "returned_for_revision"
    ESCALATED = "escalated"
    INTEGRATED = "integrated"
    COMPLETED = "completed"
    FAILED = "failed"


class FailureType(str, Enum):
    REQUIREMENT_AMBIGUITY = "requirement_ambiguity"
    ARCHITECTURE_CONFLICT = "architecture_conflict"
    IMPLEMENTATION_ERROR = "implementation_error"
    TEST_FAILURE = "test_failure"
    SECURITY_RISK = "security_risk"
    INTEGRATION_CONFLICT = "integration_conflict"
    COST_OVERRUN = "cost_overrun"
    CONTEXT_OVERFLOW = "context_overflow"
    PERMISSION_VIOLATION = "permission_violation"
    REPEATED_FAILURE = "repeated_failure"
    UNKNOWN_FAILURE = "unknown_failure"


class AutonomyLevel(str, Enum):
    MANUAL = "manual"
    SUPERVISED = "supervised"
    AUTONOMOUS_LOW_RISK = "autonomous_low_risk"
    AUTONOMOUS_WITH_BUDGET = "autonomous_with_budget"
    FULLY_BLOCKED_FOR_HIGH_RISK = "fully_blocked_for_high_risk"


class StopClassification(str, Enum):
    ESCALATE_TO_LARGE_MODEL = "escalate_to_large_model"
    REQUEST_HUMAN_APPROVAL = "request_human_approval"
    SHRINK_SCOPE = "shrink_scope"
    CREATE_CLARIFICATION_REPORT = "create_clarification_report"
    STOP_AS_FAILED = "stop_as_failed"
    DEFER_TO_BACKLOG = "defer_to_backlog"


# --------------------------------------------------------------------------- #
# Failure record + clarification
# --------------------------------------------------------------------------- #
@dataclass
class Failure:
    failure_id: str
    task_id: str
    failure_type: FailureType
    detected_by: str
    recommended_route: str            # team_id to return to, or "meta" / "human"
    recommended_action: str
    evidence: list[str] = field(default_factory=list)
    retry_allowed: bool = True
    requires_human_approval: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_id": self.failure_id,
            "task_id": self.task_id,
            "failure_type": self.failure_type.value,
            "detected_by": self.detected_by,
            "evidence": list(self.evidence),
            "recommended_route": self.recommended_route,
            "recommended_action": self.recommended_action,
            "retry_allowed": self.retry_allowed,
            "requires_human_approval": self.requires_human_approval,
        }


@dataclass
class ClarificationReport:
    """What the system assembles BEFORE asking a human (14.1)."""

    task_id: str
    what_is_unclear: list[str] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    recommendation: str = ""
    recommendation_risks: list[str] = field(default_factory=list)
    human_judgment_needed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__


# --------------------------------------------------------------------------- #
# Team Contract
# --------------------------------------------------------------------------- #
@dataclass
class TeamContract:
    team_task_id: str
    assigned_team: str
    objective: str
    input_artifacts: list[str] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)        # other team_task_ids
    downstream_teams: list[str] = field(default_factory=list)
    escalation_conditions: list[str] = field(default_factory=list)
    autonomy_level: AutonomyLevel = AutonomyLevel.SUPERVISED
    status: TeamTaskStatus = TeamTaskStatus.PLANNED
    loop_count: int = 0

    def validate(self) -> list[str]:
        errs: list[str] = []
        if not self.team_task_id:
            errs.append("team_task_id required")
        if not self.assigned_team:
            errs.append("assigned_team required")
        if not self.objective or len(self.objective.strip()) < 4:
            errs.append("objective must be a non-trivial sentence")
        vague = ("全部", "よしなに", "いい感じ", "なんとか", "everything", "as appropriate")
        if any(v in self.objective for v in vague):
            errs.append(f"objective too vague to contract: {self.objective!r}")
        if not self.expected_outputs:
            errs.append("expected_outputs required")
        if not self.acceptance_criteria:
            errs.append("acceptance_criteria required")
        if self.team_task_id in self.dependencies:
            errs.append("team cannot depend on itself")
        return errs

    @property
    def is_valid(self) -> bool:
        return not self.validate()

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_task_id": self.team_task_id,
            "assigned_team": self.assigned_team,
            "objective": self.objective,
            "input_artifacts": list(self.input_artifacts),
            "expected_outputs": list(self.expected_outputs),
            "acceptance_criteria": list(self.acceptance_criteria),
            "constraints": list(self.constraints),
            "dependencies": list(self.dependencies),
            "downstream_teams": list(self.downstream_teams),
            "escalation_conditions": list(self.escalation_conditions),
            "autonomy_level": self.autonomy_level.value,
            "status": self.status.value,
            "loop_count": self.loop_count,
        }


# --------------------------------------------------------------------------- #
# Team template (registry entry)
# --------------------------------------------------------------------------- #
@dataclass
class TeamTemplate:
    team_id: str
    team_name: str
    mission: str
    responsibilities: list[str] = field(default_factory=list)
    internal_agents: list[str] = field(default_factory=list)
    allowed_task_types: list[str] = field(default_factory=list)
    forbidden_task_types: list[str] = field(default_factory=list)
    required_inputs: list[str] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=list)
    review_requirements: list[str] = field(default_factory=list)
    escalation_rules: list[str] = field(default_factory=list)
    matches_keywords: list[str] = field(default_factory=list)
    # internal decomposition pipeline (same shape as an OrgTemplate phase)
    phases: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "TeamTemplate":
        return cls(
            team_id=d["team_id"],
            team_name=d.get("team_name", d["team_id"]),
            mission=d.get("mission", ""),
            responsibilities=list(d.get("responsibilities", [])),
            internal_agents=list(d.get("internal_agents", [])),
            allowed_task_types=list(d.get("allowed_task_types", [])),
            forbidden_task_types=list(d.get("forbidden_task_types", [])),
            required_inputs=list(d.get("required_inputs", [])),
            expected_outputs=list(d.get("expected_outputs", [])),
            review_requirements=list(d.get("review_requirements", [])),
            escalation_rules=list(d.get("escalation_rules", [])),
            matches_keywords=list(d.get("matches_keywords", [])),
            phases=list(d.get("phases", [])),
        )


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
@dataclass
class TeamResult:
    team_task_id: str
    team_id: str
    status: TeamTaskStatus
    plan: Any = None                       # tehai RunPlan
    task_results: dict = field(default_factory=dict)   # task_id -> ExecutionResult
    artifacts: dict = field(default_factory=dict)      # name -> content
    failures: list[Failure] = field(default_factory=list)
    loop_count: int = 0

    @property
    def ok(self) -> bool:
        return self.status in (TeamTaskStatus.COMPLETED, TeamTaskStatus.INTEGRATED)


@dataclass
class MetaRunResult:
    run_id: str
    goal: str
    team_composition: list[str] = field(default_factory=list)
    team_contracts: list[TeamContract] = field(default_factory=list)
    execution_order: list[str] = field(default_factory=list)
    team_results: dict = field(default_factory=dict)   # team_task_id -> TeamResult
    loop_history: list[dict] = field(default_factory=list)
    clarification_reports: list[dict] = field(default_factory=list)
    competitions: list[dict] = field(default_factory=list)
    final_status: str = "pending"
    human_intervention_required: bool = False
    assumptions: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "team_composition": list(self.team_composition),
            "team_contracts": [c.to_dict() for c in self.team_contracts],
            "execution_order": list(self.execution_order),
            "loop_history": list(self.loop_history),
            "clarification_reports": list(self.clarification_reports),
            "competitions": list(self.competitions),
            "final_status": self.final_status,
            "human_intervention_required": self.human_intervention_required,
            "assumptions": list(self.assumptions),
            "metrics": dict(self.metrics),
        }
