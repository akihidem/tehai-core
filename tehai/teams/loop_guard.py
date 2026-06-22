"""Autonomous Loop Guard (spec §13).

Reduces human intervention but forbids unbounded autonomy. Decides whether a
failed team contract may be auto-rerouted+rerun, or whether the loop must stop —
and if it stops, classifies the stop so the caller knows what to do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import AutonomyLevel, Failure, FailureType, StopClassification


@dataclass
class LoopGuardConfig:
    max_loops: int = 6                 # total reroute iterations per run
    max_same_failure: int = 3          # same failure_type N times -> stop
    max_cost: float | None = None      # None == unbounded (record only)


@dataclass
class LoopState:
    loop_count: int = 0
    total_cost: float = 0.0
    failure_type_counts: dict = field(default_factory=dict)

    def record(self, failure: Failure, cost: float = 0.0) -> int:
        self.loop_count += 1
        self.total_cost += cost
        k = failure.failure_type.value
        self.failure_type_counts[k] = self.failure_type_counts.get(k, 0) + 1
        return self.failure_type_counts[k]


@dataclass
class GuardDecision:
    allow_autorun: bool
    stop: Optional[StopClassification] = None
    reason: str = ""


# failure_types that are never auto-resolved (need human / higher authority)
_HARD_STOP_TYPES = {
    FailureType.PERMISSION_VIOLATION,
    FailureType.REPEATED_FAILURE,
}


class AutonomousLoopGuard:
    def __init__(self, config: LoopGuardConfig | None = None):
        self.config = config or LoopGuardConfig()

    def evaluate(
        self,
        failure: Failure,
        state: LoopState,
        *,
        risk_high: bool = False,
        prod_or_external: bool = False,
        autonomy: AutonomyLevel = AutonomyLevel.SUPERVISED,
        judge_low_confidence: bool = False,
        teams_in_conflict: bool = False,
        scope_change: bool = False,
        same_failure_count: int = 1,
    ) -> GuardDecision:
        cfg = self.config

        if autonomy == AutonomyLevel.MANUAL:
            return GuardDecision(False, StopClassification.REQUEST_HUMAN_APPROVAL, "autonomy=manual")
        if autonomy == AutonomyLevel.FULLY_BLOCKED_FOR_HIGH_RISK and risk_high:
            return GuardDecision(False, StopClassification.REQUEST_HUMAN_APPROVAL, "high risk fully blocked")

        if failure.requires_human_approval or failure.failure_type in _HARD_STOP_TYPES:
            return GuardDecision(False, StopClassification.REQUEST_HUMAN_APPROVAL,
                                 f"{failure.failure_type.value} requires human approval")
        if failure.failure_type == FailureType.PERMISSION_VIOLATION or prod_or_external:
            return GuardDecision(False, StopClassification.REQUEST_HUMAN_APPROVAL,
                                 "production/external/permission -> approval gate")
        if same_failure_count >= cfg.max_same_failure:
            return GuardDecision(False, StopClassification.STOP_AS_FAILED,
                                 f"same failure_type x{same_failure_count}")
        if state.loop_count >= cfg.max_loops:
            return GuardDecision(False, StopClassification.DEFER_TO_BACKLOG,
                                 f"max loops {cfg.max_loops} reached")
        if cfg.max_cost is not None and state.total_cost > cfg.max_cost:
            return GuardDecision(False, StopClassification.SHRINK_SCOPE,
                                 f"cost {state.total_cost} > cap {cfg.max_cost}")
        if failure.failure_type == FailureType.COST_OVERRUN:
            return GuardDecision(False, StopClassification.SHRINK_SCOPE, "cost overrun")
        if scope_change:
            return GuardDecision(False, StopClassification.REQUEST_HUMAN_APPROVAL, "scope change needed")
        if teams_in_conflict:
            return GuardDecision(False, StopClassification.REQUEST_HUMAN_APPROVAL, "teams in conflict")
        if judge_low_confidence:
            return GuardDecision(False, StopClassification.ESCALATE_TO_LARGE_MODEL, "judge low confidence")
        if not failure.retry_allowed:
            return GuardDecision(False, StopClassification.CREATE_CLARIFICATION_REPORT, "retry not allowed")

        # Otherwise: low/medium-risk, within budget, improvable -> auto reroute+rerun.
        return GuardDecision(True, None, f"auto reroute -> {failure.recommended_route}")
