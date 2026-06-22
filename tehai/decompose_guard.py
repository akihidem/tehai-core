"""Recursive Decomposition Guard.

"Decomposing is not progress." This guard's only job is to forbid decomposition
unless it provably moves toward a deliverable *and* stays inside hard safety
bounds. Every rejection carries a machine-readable reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .models import TaskContract


class RejectReason(str, Enum):
    OK = "ok"
    MAX_DEPTH = "max_depth_reached"
    MAX_DELEGATIONS = "max_delegations_reached"
    MAX_CONCURRENCY = "max_concurrency_reached"
    OVER_BUDGET = "cost_budget_exceeded"
    NOT_SMALLER = "children_not_smaller_than_parent"
    DUPLICATE_OBJECTIVE = "duplicate_objective"
    CYCLIC_DEPENDENCY = "cyclic_dependency"
    UNCONTRACTABLE = "child_not_contractable"
    NO_PROGRESS = "does_not_move_toward_artifact"
    NO_CHILDREN = "no_children_proposed"


@dataclass
class GuardConfig:
    max_depth: int = 3
    max_concurrent_agents: int = 8
    max_delegations: int = 20
    max_retries: int = 3
    max_cost_per_run: float | None = None  # None == unbounded (record only)


@dataclass
class DecompositionState:
    delegations_used: int = 0
    active_agents: int = 0
    total_cost: float = 0.0
    seen_objectives: set[str] = field(default_factory=set)
    retries: dict[str, int] = field(default_factory=dict)


@dataclass
class GuardDecision:
    allowed: bool
    reason: RejectReason
    detail: str = ""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _has_cycle(children: list[TaskContract]) -> bool:
    ids = {c.task_id for c in children}
    graph = {c.task_id: [d for d in c.dependencies if d in ids] for c in children}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}

    def visit(n: str) -> bool:
        color[n] = GRAY
        for m in graph[n]:
            if color[m] == GRAY:
                return True
            if color[m] == WHITE and visit(m):
                return True
        color[n] = BLACK
        return False

    return any(color[n] == WHITE and visit(n) for n in graph)


class DecompositionGuard:
    def __init__(self, config: GuardConfig | None = None):
        self.config = config or GuardConfig()

    # ----- retries ----- #
    def can_retry(self, task_id: str, state: DecompositionState) -> bool:
        return state.retries.get(task_id, 0) < self.config.max_retries

    def record_retry(self, task_id: str, state: DecompositionState) -> None:
        state.retries[task_id] = state.retries.get(task_id, 0) + 1

    # ----- core gate ----- #
    def can_decompose(
        self,
        parent: TaskContract,
        children: list[TaskContract],
        state: DecompositionState,
        estimated_cost: float = 0.0,
    ) -> GuardDecision:
        cfg = self.config

        if not children:
            return GuardDecision(False, RejectReason.NO_CHILDREN, "no children proposed")

        # Depth.
        if parent.depth + 1 > cfg.max_depth:
            return GuardDecision(
                False, RejectReason.MAX_DEPTH,
                f"parent depth {parent.depth} + 1 > max_depth {cfg.max_depth}",
            )

        # Global delegation budget.
        if state.delegations_used + len(children) > cfg.max_delegations:
            return GuardDecision(
                False, RejectReason.MAX_DELEGATIONS,
                f"{state.delegations_used}+{len(children)} > max_delegations {cfg.max_delegations}",
            )

        # Concurrency.
        if state.active_agents + len(children) > cfg.max_concurrent_agents:
            return GuardDecision(
                False, RejectReason.MAX_CONCURRENCY,
                f"{state.active_agents}+{len(children)} > max_concurrent {cfg.max_concurrent_agents}",
            )

        # Cost budget.
        if cfg.max_cost_per_run is not None and state.total_cost + estimated_cost > cfg.max_cost_per_run:
            return GuardDecision(
                False, RejectReason.OVER_BUDGET,
                f"{state.total_cost}+{estimated_cost} > budget {cfg.max_cost_per_run}",
            )

        # Each child must be contractable on its own.
        for c in children:
            errs = c.validate()
            if errs:
                return GuardDecision(
                    False, RejectReason.UNCONTRACTABLE,
                    f"child {c.task_id} invalid contract: {errs[0]}",
                )

        # Each child must be strictly smaller than the parent (estimated_steps).
        # The total across children may exceed the parent when work genuinely
        # parallelizes; we only forbid a child that is not itself smaller.
        parent_steps = max(parent.estimated_steps, 1)
        if any(c.estimated_steps >= parent_steps for c in children):
            return GuardDecision(
                False, RejectReason.NOT_SMALLER,
                "a child is not smaller (estimated_steps) than the parent",
            )

        # Duplicate-objective: against history and within the batch.
        batch_norms = [_norm(c.objective) for c in children]
        for c, n in zip(children, batch_norms):
            if n in state.seen_objectives:
                return GuardDecision(
                    False, RejectReason.DUPLICATE_OBJECTIVE,
                    f"objective already seen: {c.objective!r}",
                )
        if len(set(batch_norms)) != len(batch_norms):
            return GuardDecision(
                False, RejectReason.DUPLICATE_OBJECTIVE,
                "two proposed children share an objective",
            )

        # No dependency cycles among children.
        if _has_cycle(children):
            return GuardDecision(False, RejectReason.CYCLIC_DEPENDENCY, "cycle among children")

        # Must move toward the parent's artifact: children's expected_output must
        # collectively be non-empty and overlap (or refine) the parent's outputs.
        child_outputs = {o for c in children for o in c.expected_output}
        if not child_outputs:
            return GuardDecision(
                False, RejectReason.NO_PROGRESS, "children declare no expected_output",
            )
        if parent.expected_output:
            overlap = child_outputs & set(parent.expected_output)
            refines = any(
                any(po in co or co in po for po in parent.expected_output)
                for co in child_outputs
            )
            if not overlap and not refines:
                return GuardDecision(
                    False, RejectReason.NO_PROGRESS,
                    "child outputs neither match nor refine the parent's expected_output",
                )

        return GuardDecision(True, RejectReason.OK, "decomposition permitted")

    def register(
        self,
        children: list[TaskContract],
        state: DecompositionState,
        estimated_cost: float = 0.0,
    ) -> None:
        """Commit an approved decomposition into the running state."""
        state.delegations_used += len(children)
        state.total_cost += estimated_cost
        for c in children:
            state.seen_objectives.add(_norm(c.objective))
