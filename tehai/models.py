"""Core data models for the tehai delegation layer.

Everything here is plain stdlib: dataclasses + enums. No third-party deps.

The center of gravity is the :class:`TaskContract`. Nothing in the system is
allowed to execute against a contract that does not validate (see ``validate``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class ModelTier(str, Enum):
    """Coarse capability/cost tiers. Cheapest capable tier wins by default."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"

    @property
    def rank(self) -> int:
        return {"small": 0, "medium": 1, "large": 2}[self.value]


class Effort(str, Enum):
    """Reasoning effort — distinct from model tier. Tier = *which* model (cost/
    capability); effort = *how hard it thinks* (reasoning/token budget). A hard,
    ambiguous task warrants more effort even at the same tier."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"

    @property
    def rank(self) -> int:
        return {"low": 0, "medium": 1, "high": 2, "xhigh": 3, "max": 4}[self.value]


def max_effort(a: "Effort", b: "Effort") -> "Effort":
    return a if a.rank >= b.rank else b


# Default effort baseline derived from an agent's model tier.
TIER_DEFAULT_EFFORT = {"small": Effort.LOW, "medium": Effort.MEDIUM, "large": Effort.HIGH}


class TaskType(str, Enum):
    """What kind of work a task is. Drives routing and review composition."""

    CLASSIFICATION = "classification"
    EXTRACTION = "extraction"
    DOC_FORMATTING = "doc_formatting"
    SUMMARIZATION = "summarization"
    RESEARCH = "research"
    SPEC_DESIGN = "spec_design"
    ARCHITECTURE = "architecture"
    CODE_IMPLEMENTATION = "code_implementation"
    CODE_REVIEW = "code_review"
    TEST_AUTHORING = "test_authoring"
    QA = "qa"
    SECURITY_REVIEW = "security_review"
    RELEASE = "release"
    INCIDENT_RESPONSE = "incident_response"
    CONTENT = "content"
    INTEGRATION = "integration"
    GENERIC = "generic"


class TaskStatus(str, Enum):
    """Task lifecycle. See the state machine in README / models docstring."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    BLOCKED = "blocked"
    REVIEWING = "reviewing"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    RETRYING = "retrying"
    ESCALATED = "escalated"
    COMPLETED = "completed"
    FAILED = "failed"


# Legal lifecycle transitions. Used by callers that want to enforce the FSM.
TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.READY, TaskStatus.BLOCKED, TaskStatus.FAILED},
    TaskStatus.READY: {TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.ESCALATED},
    TaskStatus.RUNNING: {TaskStatus.REVIEWING, TaskStatus.FAILED, TaskStatus.ESCALATED, TaskStatus.BLOCKED},
    TaskStatus.BLOCKED: {TaskStatus.READY, TaskStatus.FAILED, TaskStatus.ESCALATED},
    TaskStatus.REVIEWING: {TaskStatus.ACCEPTED, TaskStatus.REJECTED, TaskStatus.ESCALATED},
    TaskStatus.REJECTED: {TaskStatus.RETRYING, TaskStatus.ESCALATED, TaskStatus.FAILED},
    TaskStatus.RETRYING: {TaskStatus.RUNNING, TaskStatus.ESCALATED, TaskStatus.FAILED},
    TaskStatus.ACCEPTED: {TaskStatus.COMPLETED},
    TaskStatus.ESCALATED: {TaskStatus.READY, TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.COMPLETED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
}


class ReviewLens(str, Enum):
    """A single, *separated* review viewpoint. Never blur two lenses into one."""

    REQUIREMENTS = "requirements"      # does it satisfy acceptance_criteria?
    EDGE_CASES = "edge_cases"          # failure / boundary conditions only
    SECURITY = "security"              # auth, secrets, injection, privilege
    UX = "ux"                          # user-facing behaviour
    TESTS = "tests"                    # does the test evidence hold up?
    AUTO_CHECK = "auto_check"          # deterministic validators (lint/schema/test run)


class JudgeDecision(str, Enum):
    ACCEPT = "accept"
    REVISE = "revise"
    DISCARD = "discard"
    RERUN = "rerun"


# --------------------------------------------------------------------------- #
# Capabilities / actions
# --------------------------------------------------------------------------- #
class Action(str, Enum):
    """Concrete capabilities an agent may be granted. Least privilege by default."""

    # read-only
    READ_FILE = "read_file"
    READ_REPOSITORY = "read_repository"
    SEARCH = "search"
    RUN_STATIC_ANALYSIS = "run_static_analysis"
    WEB_FETCH = "web_fetch"
    # write-but-local
    WRITE_FILE = "write_file"
    WRITE_REVIEW_REPORT = "write_review_report"
    RUN_TEST = "run_test"
    # dangerous / outward-facing (require an approval gate)
    FILE_DELETE = "file_delete"
    GIT_PUSH = "git_push"
    PRODUCTION_DEPLOY = "production_deploy"
    EXTERNAL_API = "external_api"
    SEND_EMAIL = "send_email"
    SEND_SLACK = "send_slack"
    DB_UPDATE = "db_update"
    SECRET_READ = "secret_read"
    SECRET_EXPORT = "secret_export"
    PERMISSION_CHANGE = "permission_change"
    DIRECT_CODE_MERGE = "direct_code_merge"


# Anything in this set must pass an Approval Gate before it runs (see permissions.py).
DANGEROUS_ACTIONS: frozenset[Action] = frozenset(
    {
        Action.FILE_DELETE,
        Action.GIT_PUSH,
        Action.PRODUCTION_DEPLOY,
        Action.EXTERNAL_API,
        Action.SEND_EMAIL,
        Action.SEND_SLACK,
        Action.DB_UPDATE,
        Action.SECRET_READ,
        Action.SECRET_EXPORT,
        Action.PERMISSION_CHANGE,
        Action.DIRECT_CODE_MERGE,
    }
)


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
# The model-router weights. Deliberately *not* complexity-only.
ROUTER_WEIGHTS: dict[str, float] = {
    "complexity": 0.25,
    "ambiguity": 0.20,
    "risk": 0.20,
    "context_size": 0.15,
    "tool_risk": 0.10,
    "domain_specialization": 0.10,
}


@dataclass
class ScoreProfile:
    """Six 0-100 axes that feed the model router. See ``ROUTER_WEIGHTS``."""

    complexity: int = 0
    ambiguity: int = 0
    risk: int = 0
    context_size: int = 0
    tool_risk: int = 0
    domain_specialization: int = 0

    def weighted_score(self) -> float:
        return (
            self.complexity * ROUTER_WEIGHTS["complexity"]
            + self.ambiguity * ROUTER_WEIGHTS["ambiguity"]
            + self.risk * ROUTER_WEIGHTS["risk"]
            + self.context_size * ROUTER_WEIGHTS["context_size"]
            + self.tool_risk * ROUTER_WEIGHTS["tool_risk"]
            + self.domain_specialization * ROUTER_WEIGHTS["domain_specialization"]
        )

    def errors(self) -> list[str]:
        out: list[str] = []
        for name in ROUTER_WEIGHTS:
            v = getattr(self, name)
            if not isinstance(v, int) or not (0 <= v <= 100):
                out.append(f"score '{name}' must be an int in [0,100], got {v!r}")
        return out


# --------------------------------------------------------------------------- #
# Task Contract — the central object
# --------------------------------------------------------------------------- #
@dataclass
class TaskContract:
    task_id: str
    objective: str
    task_type: TaskType
    parent_task_id: Optional[str] = None

    input_artifacts: list[str] = field(default_factory=list)
    expected_output: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)

    estimated_context_tokens: int = 0
    estimated_steps: int = 1

    scores: ScoreProfile = field(default_factory=ScoreProfile)

    recommended_model: ModelTier = ModelTier.SMALL
    recommended_effort: Effort = Effort.MEDIUM
    assigned_agent_template: Optional[str] = None
    dependencies: list[str] = field(default_factory=list)
    escalation_conditions: list[str] = field(default_factory=list)
    done_definition: list[str] = field(default_factory=list)

    depth: int = 0
    status: TaskStatus = TaskStatus.PENDING

    # ----- convenience score accessors (keep the spec's flat field names) ----- #
    @property
    def complexity_score(self) -> int:
        return self.scores.complexity

    @property
    def ambiguity_score(self) -> int:
        return self.scores.ambiguity

    @property
    def risk_score(self) -> int:
        return self.scores.risk

    @property
    def tool_risk_score(self) -> int:
        return self.scores.tool_risk

    def validate(self) -> list[str]:
        """Return a list of human-readable errors. Empty list == valid.

        A contract that fails validation MUST NOT be executed; it is returned
        to the parent for repair (see Orchestrator / Executor).
        """
        errs: list[str] = []
        if not self.task_id or not isinstance(self.task_id, str):
            errs.append("task_id is required")
        if not self.objective or len(self.objective.strip()) < 4:
            errs.append("objective must be a non-trivial sentence")
        # Reject vague, non-contractable objectives outright.
        vague = ("全部", "よしなに", "いい感じ", "なんとか", "全体を理解",
                 "everything", "as appropriate", "figure it out", "somehow")
        low = self.objective.lower()
        if any(v in self.objective or v in low for v in vague):
            errs.append(f"objective is too vague to contract: {self.objective!r}")
        if not isinstance(self.task_type, TaskType):
            errs.append("task_type must be a TaskType")
        if not self.expected_output:
            errs.append("expected_output must name at least one artifact")
        if not self.acceptance_criteria:
            errs.append("acceptance_criteria must have at least one testable/reviewable item")
        if not self.escalation_conditions:
            errs.append("escalation_conditions must be defined")
        if self.estimated_context_tokens < 0:
            errs.append("estimated_context_tokens must be >= 0")
        if self.estimated_steps < 1:
            errs.append("estimated_steps must be >= 1")
        if self.depth < 0:
            errs.append("depth must be >= 0")
        if self.parent_task_id is not None and self.parent_task_id == self.task_id:
            errs.append("task cannot be its own parent")
        if self.task_id in self.dependencies:
            errs.append("task cannot depend on itself")
        errs.extend(self.scores.errors())
        return errs

    @property
    def is_valid(self) -> bool:
        return not self.validate()

    # ----- serialization (flat, schema-friendly) ----- #
    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "objective": self.objective,
            "task_type": self.task_type.value,
            "input_artifacts": list(self.input_artifacts),
            "expected_output": list(self.expected_output),
            "acceptance_criteria": list(self.acceptance_criteria),
            "constraints": list(self.constraints),
            "required_tools": list(self.required_tools),
            "estimated_context_tokens": self.estimated_context_tokens,
            "estimated_steps": self.estimated_steps,
            "complexity_score": self.scores.complexity,
            "ambiguity_score": self.scores.ambiguity,
            "risk_score": self.scores.risk,
            "context_size_score": self.scores.context_size,
            "tool_risk_score": self.scores.tool_risk,
            "domain_specialization_score": self.scores.domain_specialization,
            "recommended_model": self.recommended_model.value,
            "recommended_effort": self.recommended_effort.value,
            "assigned_agent_template": self.assigned_agent_template,
            "dependencies": list(self.dependencies),
            "escalation_conditions": list(self.escalation_conditions),
            "done_definition": list(self.done_definition),
            "depth": self.depth,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskContract":
        return cls(
            task_id=d["task_id"],
            objective=d["objective"],
            task_type=TaskType(d["task_type"]),
            parent_task_id=d.get("parent_task_id"),
            input_artifacts=list(d.get("input_artifacts", [])),
            expected_output=list(d.get("expected_output", [])),
            acceptance_criteria=list(d.get("acceptance_criteria", [])),
            constraints=list(d.get("constraints", [])),
            required_tools=list(d.get("required_tools", [])),
            estimated_context_tokens=int(d.get("estimated_context_tokens", 0)),
            estimated_steps=int(d.get("estimated_steps", 1)),
            scores=ScoreProfile(
                complexity=int(d.get("complexity_score", 0)),
                ambiguity=int(d.get("ambiguity_score", 0)),
                risk=int(d.get("risk_score", 0)),
                context_size=int(d.get("context_size_score", 0)),
                tool_risk=int(d.get("tool_risk_score", 0)),
                domain_specialization=int(d.get("domain_specialization_score", 0)),
            ),
            recommended_model=ModelTier(d.get("recommended_model", "small")),
            recommended_effort=Effort(d.get("recommended_effort", "medium")),
            assigned_agent_template=d.get("assigned_agent_template"),
            dependencies=list(d.get("dependencies", [])),
            escalation_conditions=list(d.get("escalation_conditions", [])),
            done_definition=list(d.get("done_definition", [])),
            depth=int(d.get("depth", 0)),
            status=TaskStatus(d.get("status", "pending")),
        )


# --------------------------------------------------------------------------- #
# Agent template
# --------------------------------------------------------------------------- #
@dataclass
class AgentTemplate:
    agent_template_id: str
    role: str
    mission: str
    responsibilities: list[str] = field(default_factory=list)
    allowed_actions: list[str] = field(default_factory=list)
    forbidden_actions: list[str] = field(default_factory=list)
    required_context: list[str] = field(default_factory=list)
    output_schema: str = ""
    quality_bar: list[str] = field(default_factory=list)
    escalation_rules: list[str] = field(default_factory=list)
    recommended_model_tier: ModelTier = ModelTier.MEDIUM
    recommended_effort: Effort = Effort.MEDIUM
    handles_task_types: list[str] = field(default_factory=list)

    def allowed(self) -> set[Action]:
        return {Action(a) for a in self.allowed_actions if a in Action._value2member_map_}

    def forbidden(self) -> set[Action]:
        return {Action(a) for a in self.forbidden_actions if a in Action._value2member_map_}

    def errors(self) -> list[str]:
        errs: list[str] = []
        if not self.agent_template_id:
            errs.append("agent_template_id required")
        if not self.mission:
            errs.append("mission required")
        overlap = set(self.allowed_actions) & set(self.forbidden_actions)
        if overlap:
            errs.append(f"actions both allowed and forbidden: {sorted(overlap)}")
        unknown = [a for a in self.allowed_actions + self.forbidden_actions
                   if a not in Action._value2member_map_]
        if unknown:
            errs.append(f"unknown actions: {sorted(set(unknown))}")
        return errs

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentTemplate":
        return cls(
            agent_template_id=d["agent_template_id"],
            role=d.get("role", d["agent_template_id"]),
            mission=d.get("mission", ""),
            responsibilities=list(d.get("responsibilities", [])),
            allowed_actions=list(d.get("allowed_actions", [])),
            forbidden_actions=list(d.get("forbidden_actions", [])),
            required_context=list(d.get("required_context", [])),
            output_schema=d.get("output_schema", ""),
            quality_bar=list(d.get("quality_bar", [])),
            escalation_rules=list(d.get("escalation_rules", [])),
            recommended_model_tier=ModelTier(d.get("recommended_model_tier", "medium")),
            recommended_effort=Effort(d["recommended_effort"]) if "recommended_effort" in d
            else TIER_DEFAULT_EFFORT[d.get("recommended_model_tier", "medium")],
            handles_task_types=list(d.get("handles_task_types", [])),
        )


# --------------------------------------------------------------------------- #
# Organization template
# --------------------------------------------------------------------------- #
@dataclass
class Phase:
    """One node of an organization template's default pipeline."""

    key: str
    title: str
    role: str                       # agent_template_id expected to own this phase
    task_type: TaskType
    depends_on: list[str] = field(default_factory=list)  # other phase keys


@dataclass
class OrgTemplate:
    org_template_id: str
    name: str
    description: str
    roles: list[str] = field(default_factory=list)          # agent_template_ids
    phases: list[Phase] = field(default_factory=list)
    matches_keywords: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Review / Judge
# --------------------------------------------------------------------------- #
@dataclass
class ReviewStep:
    lens: ReviewLens
    agent_template_id: str
    model_tier: ModelTier


@dataclass
class ReviewPlan:
    task_id: str
    steps: list[ReviewStep] = field(default_factory=list)
    require_judge: bool = False
    require_human_gate: bool = False
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "steps": [
                {"lens": s.lens.value, "agent_template_id": s.agent_template_id,
                 "model_tier": s.model_tier.value}
                for s in self.steps
            ],
            "require_judge": self.require_judge,
            "require_human_gate": self.require_human_gate,
            "rationale": self.rationale,
        }


@dataclass
class ReviewResult:
    task_id: str
    lens: ReviewLens
    verdict: str            # "pass" | "fail" | "concerns"
    severity: str = "none"  # none | low | medium | high | critical
    rationale: str = ""
    findings: list[str] = field(default_factory=list)


@dataclass
class Decision:
    task_id: str
    decision: JudgeDecision
    reason: str
    basis: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "decision": self.decision.value,
            "reason": self.reason,
            "basis": list(self.basis),
        }


# --------------------------------------------------------------------------- #
# Artifact + provenance
# --------------------------------------------------------------------------- #
@dataclass
class Provenance:
    created_by_agent: str
    task_contract_id: str
    derived_from: list[str] = field(default_factory=list)
    reviews_passed: list[str] = field(default_factory=list)
    judged_by: Optional[str] = None
    judge_decision: Optional[str] = None


@dataclass
class Artifact:
    name: str
    provenance: Provenance


# --------------------------------------------------------------------------- #
# Run plan (the orchestrator's output)
# --------------------------------------------------------------------------- #
@dataclass
class RunPlan:
    run_id: str
    request: str
    task_type: TaskType
    org_template_id: str
    contracts: list[TaskContract] = field(default_factory=list)
    execution_order: list[str] = field(default_factory=list)
    review_plans: dict[str, ReviewPlan] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    decomposition_strategy: str = ""   # e.g. "code_implementation:template" | ":llm"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "request": self.request,
            "task_type": self.task_type.value,
            "org_template_id": self.org_template_id,
            "decomposition_strategy": self.decomposition_strategy,
            "contracts": [c.to_dict() for c in self.contracts],
            "execution_order": list(self.execution_order),
            "review_plans": {k: v.to_dict() for k, v in self.review_plans.items()},
            "assumptions": list(self.assumptions),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
