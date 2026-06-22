"""Shared test helpers."""

from __future__ import annotations

from tehai.models import ScoreProfile, TaskContract, TaskType


def mk_contract(**over) -> TaskContract:
    """Build a minimal *valid* TaskContract; override any field via kwargs."""
    base = dict(
        task_id="T-1",
        objective="implement input validation for the login form",
        task_type=TaskType.CODE_IMPLEMENTATION,
        expected_output=["validation.ts"],
        acceptance_criteria=["rejects malformed email"],
        escalation_conditions=["spec conflict detected"],
        estimated_steps=8,
        scores=ScoreProfile(complexity=40, ambiguity=20, risk=20,
                            context_size=10, tool_risk=15, domain_specialization=30),
    )
    base.update(over)
    return TaskContract(**base)
